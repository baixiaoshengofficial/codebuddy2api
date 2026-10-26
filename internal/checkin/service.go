package checkin

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/jsonfile"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/upstream"
)

type Settings interface {
	Enabled() bool
	Schedule() string
	Get(string) string
}
type StateStore interface {
	Load() (Data, error)
	Save(Data) error
}
type JSONState struct{ Path string }

func (s JSONState) Load() (Data, error) {
	d := Data{}
	err := jsonfile.Read(s.Path, &d)
	if os.IsNotExist(err) {
		err = nil
	}
	return d, err
}
func (s JSONState) Save(d Data) error { return jsonfile.Write(s.Path, d) }

type Refresher interface {
	Ensure(context.Context, credential.Selection, bool) (credential.Selection, error)
}
type Notifier interface{ Notify(context.Context, Data) }
type Service struct {
	Repo        credential.Lister
	Refresh     Refresher
	Billing     Billing
	Settings    Settings
	Persistence StateStore
	Notifier    Notifier
	Now         func() time.Time
	mu          sync.RWMutex
	runMu       sync.Mutex
	state       Data
	running     bool
}

func New(repo credential.Lister, refresh Refresher, billing Billing, settings Settings, state StateStore) (*Service, error) {
	stored, err := state.Load()
	if err != nil {
		return nil, err
	}
	if stored == nil {
		stored = Data{}
	}
	return &Service{Repo: repo, Refresh: refresh, Billing: billing, Settings: settings, Persistence: state, state: stored}, nil
}

var China = time.FixedZone("CST", 8*3600)

func (s *Service) now() time.Time {
	if s.Now != nil {
		return s.Now().In(China)
	}
	return time.Now().In(China)
}
func (s *Service) base(r credential.Record) Data {
	return Data{"filename": r.Filename, "user_id": r.Data.Text("user_id"), "status": "pending", "message": "等待检查", "checked_at": nil, "checkin_date": s.now().Format("2006-01-02"), "active": nil, "today_checked_in": false, "streak_days": 0, "today_credit": 0, "total_credits": 0, "week_progress": []any{}}
}
func (s *Service) next(state Data) time.Time {
	now := s.now()
	hour, minute := 11, 0
	fmt.Sscanf(s.Settings.Schedule(), "%d:%d", &hour, &minute)
	target := time.Date(now.Year(), now.Month(), now.Day(), hour, minute, 0, 0, China)
	if state.Text("last_scheduled_date") == now.Format("2006-01-02") {
		if retry := state.Number("scheduled_retry_at"); retry > 0 {
			return time.Unix(retry, 0)
		}
		return target.AddDate(0, 0, 1)
	}
	if now.After(target) {
		return now
	}
	return target
}
func (s *Service) Status() (Data, error) {
	records, err := s.Repo.List()
	if err != nil {
		return nil, err
	}
	s.mu.RLock()
	state := credential.Clone(s.state)
	running := s.running
	s.mu.RUnlock()
	accounts := []Data{}
	stored := state.Object("accounts")
	for _, record := range records {
		result := stored.Object(record.Filename)
		if len(result) == 0 || (result.Text("status") == "expired" && record.Data.Number("created_at") > result.Number("checked_at") && !credential.Expired(record.Data, s.now())) {
			result = s.base(record)
		}
		accounts = append(accounts, result)
	}
	var next, message any
	if s.Settings.Enabled() {
		next = s.next(state).Unix()
	}
	if len(records) == 0 {
		message = "请先授权 WorkBuddy"
	}
	return Data{"enabled": s.Settings.Enabled(), "authorized": len(records) > 0, "message": message, "schedule_time": s.Settings.Schedule(), "running": running, "last_run_at": state["last_run_at"], "last_scheduled_date": state["last_scheduled_date"], "next_run_at": next, "bark_url": s.Settings.Get("CODEBUDDY_BARK_URL"), "accounts": accounts}, nil
}
func merge(result, data Data) {
	for _, key := range strings.Split("active,today_checked_in,streak_days,today_credit,daily_credit,total_credits,week_progress,week_checkin_days,checkin_dates,is_streak_day,next_streak_day,streak_bonus_days,streak_bonus_credit,activity_name,theme_name,season,start_time,end_time,claim_button_text,action_button", ",") {
		if data[key] != nil {
			result[key] = data[key]
		}
	}
	if data["credit"] != nil {
		result["today_credit"] = data["credit"]
		result["daily_credit"] = data["credit"]
	}
}
func retryable(err error) bool { f, ok := err.(*fault.Error); return ok && f.Status == 503 }
func wait(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
func (s *Service) checkOnce(ctx context.Context, record credential.Record) (Data, error) {
	result := s.base(record)
	result["checked_at"] = s.now().Unix()
	data, err := s.Billing.Status(ctx, record.Data)
	if err != nil {
		return result, err
	}
	merge(result, data)
	if data["today_checked_in"] == true {
		result["status"] = "already_checked_in"
		result["message"] = "今日奖励已领取"
		return result, nil
	}
	if data["active"] != true {
		result["status"] = "inactive"
		result["message"] = "当前账号未开放签到活动"
		return result, nil
	}
	claimed, err := s.Billing.Claim(ctx, record.Data)
	if err != nil {
		return result, err
	}
	merge(result, claimed)
	result["status"] = "claimed"
	result["message"] = "签到成功，奖励已领取"
	if claimed["_already"] == true {
		result["status"] = "already_checked_in"
		result["message"] = "今日奖励已领取"
	} else {
		result["claimed_at"] = s.now().Unix()
	}
	result["today_checked_in"] = true
	if refreshed, err := s.Billing.Status(ctx, record.Data); err == nil {
		merge(result, refreshed)
	}
	return result, nil
}
func (s *Service) check(ctx context.Context, record credential.Record) Data {
	selection := credential.Selection{Record: record}
	fresh, err := s.Refresh.Ensure(ctx, selection, false)
	if err == nil {
		record = fresh.Record
	}
	refreshed := false
	var result Data
	if err == nil {
		for attempt := 0; attempt < 3; attempt++ {
			result, err = s.checkOnce(ctx, record)
			if f, ok := err.(*fault.Error); ok && f.Status == 401 && !refreshed && record.Data.Text("refresh_token") != "" {
				refreshed = true
				fresh, refreshErr := s.Refresh.Ensure(ctx, credential.Selection{Record: record}, true)
				if refreshErr == nil {
					record = fresh.Record
					result, err = s.checkOnce(ctx, record)
				} else {
					err = refreshErr
				}
			}
			if !retryable(err) || attempt == 2 {
				break
			}
			if err = wait(ctx, []time.Duration{2 * time.Second, 5 * time.Second}[attempt]); err != nil {
				break
			}
		}
	}
	if result == nil {
		result = s.base(record)
		result["checked_at"] = s.now().Unix()
	}
	if err != nil {
		result["status"] = "error"
		result["message"] = "签到检查失败"
		if f, ok := err.(*fault.Error); ok {
			result["message"] = f.Message
			if f.Status == 401 {
				result["status"] = "expired"
			}
		}
		if retryable(err) {
			result["retryable"] = true
		}
	}
	return result
}
func (s *Service) Run(ctx context.Context, force, scheduled bool) (Data, error) {
	s.runMu.Lock()
	defer s.runMu.Unlock()
	s.mu.Lock()
	s.running = true
	state := credential.Clone(s.state)
	s.mu.Unlock()
	defer func() { s.mu.Lock(); s.running = false; s.mu.Unlock() }()
	records, err := s.Repo.List()
	if err != nil {
		return nil, err
	}
	today := s.now().Format("2006-01-02")
	stored := state.Object("accounts")
	accounts := Data{}
	hasRetry := false
	for _, record := range records {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		result := stored.Object(record.Filename)
		if force || result.Text("checkin_date") != today || (result.Text("status") != "claimed" && result.Text("status") != "already_checked_in") {
			result = s.check(ctx, record)
		}
		accounts[record.Filename] = result
		if result["retryable"] == true {
			hasRetry = true
		}
	}
	state["accounts"] = accounts
	state["last_run_at"] = s.now().Unix()
	if scheduled {
		count := int64(0)
		if state.Text("last_scheduled_date") == today {
			count = state.Number("scheduled_retry_count")
		}
		state["last_scheduled_date"] = today
		if hasRetry && count < 3 {
			state["scheduled_retry_count"] = count + 1
			state["scheduled_retry_at"] = s.now().Add(5 * time.Minute).Unix()
		} else {
			state["scheduled_retry_count"] = 0
			state["scheduled_retry_at"] = nil
		}
	} else if state.Text("last_scheduled_date") == today && !hasRetry {
		state["scheduled_retry_count"] = 0
		state["scheduled_retry_at"] = nil
	}
	if err := s.Persistence.Save(state); err != nil {
		return nil, err
	}
	s.mu.Lock()
	s.state = state
	s.running = false
	s.mu.Unlock()
	status, err := s.Status()
	if err == nil && scheduled && len(records) > 0 && s.Notifier != nil {
		s.Notifier.Notify(ctx, status)
	}
	return status, err
}
func (s *Service) Loop(ctx context.Context) {
	for {
		if s.Settings.Enabled() {
			s.mu.RLock()
			state := credential.Clone(s.state)
			s.mu.RUnlock()
			if !s.next(state).After(s.now()) {
				if _, err := s.Run(ctx, false, true); err != nil && ctx.Err() == nil {
					slog.Error("Scheduled check-in failed", "error", err)
				}
			}
		}
		if wait(ctx, 30*time.Second) != nil {
			return
		}
	}
}

type Bark struct {
	Client upstream.Doer
	URL    func() string
}

func (b Bark) Notify(ctx context.Context, status Data) {
	base := strings.TrimRight(b.URL(), "/")
	if base == "" {
		return
	}
	accounts, _ := status["accounts"].([]Data)
	lines := []string{}
	for _, a := range accounts {
		lines = append(lines, a.Text("user_id")+": "+a.Text("message"))
	}
	title := "CodeBuddy 每日签到"
	body := strings.Join(lines, "\n")
	_, code, err := upstream.JSON(ctx, b.Client, "POST", base, http.Header{"Content-Type": {"application/json"}}, Data{"title": title, "body": body, "group": "CodeBuddy签到"})
	if code >= 400 {
		request, _ := http.NewRequestWithContext(ctx, "GET", base+"/"+url.PathEscape(title)+"/"+url.PathEscape(body), nil)
		response, e := b.Client.Do(request)
		if e == nil {
			response.Body.Close()
		}
		err = e
	}
	if err != nil {
		slog.Warn("Bark notification failed")
	}
}
