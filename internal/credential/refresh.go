package credential

import (
	"context"
	"fmt"
	"net/http"
	"sync"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/upstream"
)

// Refresher serializes renewal per account; chat transport never retries POSTs.
type Refresher struct {
	Repo     Updater
	Client   upstream.Doer
	Endpoint func() string
	Product  string
	Now      func() time.Time
	locks    sync.Map
}

func (r *Refresher) now() time.Time {
	if r.Now != nil {
		return r.Now()
	}
	return time.Now()
}
func (r *Refresher) Ensure(ctx context.Context, selected Selection, force bool) (Selection, error) {
	lock, _ := r.locks.LoadOrStore(selected.Filename, &sync.Mutex{})
	mu := lock.(*sync.Mutex)
	mu.Lock()
	defer mu.Unlock()
	data, err := r.Repo.Get(selected.Filename)
	if err != nil {
		return selected, err
	}
	if data.Number("session_dead_at") > 0 {
		return selected, fault.New(401, "session_expired", "凭证已失效，请重新授权")
	}
	expired := Expired(data, r.now())
	if expired && data.Text("refresh_token") == "" {
		return selected, fault.New(401, "session_expired", "凭证已过期，请重新授权")
	}
	age := r.now().Unix() - data.Number("last_refresh_at")
	if data.Number("last_refresh_at") == 0 {
		age = r.now().Unix() - data.Number("created_at")
	}
	// A concurrent caller may already have completed this renewal.
	if force && data.Text("bearer_token") != selected.Data.Text("bearer_token") {
		force = false
	}
	if data.Text("refresh_token") != "" && (force || expired || age >= 20*3600) {
		fresh, refreshErr := r.refresh(ctx, data)
		if refreshErr != nil {
			if f, ok := refreshErr.(*fault.Error); ok && f.Status == 401 {
				dead := Clone(data)
				dead["session_dead_at"] = r.now().Unix()
				dead["session_dead_reason"] = "12153"
				if err := r.Repo.CompareAndSwap(selected.Filename, data.Text("bearer_token"), dead); err != nil {
					return selected, err
				}
				return selected, refreshErr
			}
			if expired || force {
				return selected, fault.New(503, "refresh_unavailable", "凭证续期暂时失败，请稍后重试")
			}
		} else {
			if err := r.Repo.CompareAndSwap(selected.Filename, data.Text("bearer_token"), fresh); err != nil {
				return selected, err
			}
			data = fresh
		}
	}
	selected.Data = data
	return selected, nil
}
func (r *Refresher) refresh(ctx context.Context, data Data) (Data, error) {
	base := r.Endpoint()
	switch data.Text("domain") {
	case "www.codebuddy.cn", "www.codebuddy.ai", "www.workbuddy.ai":
		base = "https://" + data.Text("domain")
	}
	agent := "CodeBuddy/5.2.5"
	if r.Product == "workbuddy" {
		agent = "WorkBuddy/5.2.5"
	}
	headers := http.Header{"Accept": {"application/json, text/plain, */*"}, "Content-Type": {"application/json"}, "X-Requested-With": {"XMLHttpRequest"}, "Origin": {base}, "Referer": {base + "/"}, "User-Agent": {agent}, "X-Refresh-Token": {data.Text("refresh_token")}, "X-Auth-Refresh-Source": {r.Product}}
	if enterprise := first(data, "enterprise_id", "enterpriseId"); enterprise != "" {
		headers.Set("X-Enterprise-Id", enterprise)
	}
	payload, status, err := upstream.JSON(ctx, r.Client, "POST", base+"/v2/plugin/auth/token/refresh", headers, Data{})
	if status == 401 || status == 403 || Data(payload).Number("code") == 12153 {
		return nil, fault.New(401, "session_expired", "凭证已失效，请重新授权")
	}
	if err != nil {
		return nil, err
	}
	code := Data(payload).Number("code")
	if status != 200 || payload["code"] == nil || (code != 0 && !(r.Product == "workbuddy" && code == 200)) {
		return nil, fmt.Errorf("upstream renewal rejected")
	}
	fresh := Data(payload).Object("data")
	token, ok := fresh["accessToken"].(string)
	if !ok || token == "" {
		return nil, fmt.Errorf("upstream renewal missing token")
	}
	out := Clone(data)
	out["bearer_token"] = token
	out["created_at"] = r.now().Unix()
	out["last_refresh_at"] = r.now().Unix()
	if data["access_token"] != nil {
		out["access_token"] = token
	}
	for from, to := range map[string]string{"refreshToken": "refresh_token", "domain": "domain"} {
		if fresh.Text(from) != "" {
			out[to] = fresh[from]
		}
	}
	ttl := fresh.Number("expiresIn")
	if ttl == 0 && Claims(token).Number("exp") > 0 {
		ttl = Claims(token).Number("exp") - r.now().Unix()
	}
	if ttl > 0 {
		out["expires_in"] = ttl
	}
	return out, nil
}

// Ready composes routing with renewal without knowing how credentials are stored.
type Freshener interface {
	Ensure(context.Context, Selection, bool) (Selection, error)
}
type RouteSelector interface {
	Select(string, *int) (Selection, error)
}
type Ready struct {
	Router    RouteSelector
	Refresher Freshener
}

func (r Ready) Select(ctx context.Context, filename string, index *int) (Selection, error) {
	seen := map[string]bool{}
	for {
		selection, err := r.Router.Select(filename, index)
		if err != nil {
			return Selection{}, err
		}
		if seen[selection.Filename] {
			return Selection{}, fault.New(401, "session_expired", "CodeBuddy 凭证已失效，请重新授权")
		}
		seen[selection.Filename] = true
		fresh, err := r.Refresher.Ensure(ctx, selection, false)
		if err == nil {
			return fresh, nil
		}
		if f, ok := err.(*fault.Error); !ok || f.Status != 401 || filename != "" || index != nil || !selection.Auto {
			return Selection{}, err
		}
	}
}
