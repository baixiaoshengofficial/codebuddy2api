package quota

import (
	"context"
	"crypto/sha256"
	"errors"
	"sync"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
)

type Repository interface {
	credential.Lister
	credential.Reader
}
type Refresher interface {
	Ensure(context.Context, credential.Selection, bool) (credential.Selection, error)
}
type Reader interface {
	Query(context.Context, credential.Data) (Balance, error)
}
type Result struct {
	Filename  string   `json:"filename"`
	Status    string   `json:"status"`
	Message   string   `json:"message,omitempty"`
	Balance   *Balance `json:"balance"`
	CheckedAt int64    `json:"checked_at"`
	UpdatedAt *int64   `json:"updated_at"`
	Stale     bool     `json:"stale"`
}
type entry struct {
	gate        chan struct{}
	fingerprint [32]byte
	result      Result
	checked     time.Time
}
type Service struct {
	repo     Repository
	refresh  Refresher
	provider Reader
	scope    func() string
	mu       sync.Mutex
	entries  map[string]*entry
	slots    chan struct{}
	Now      func() time.Time
}

func New(repo Repository, refresh Refresher, provider Reader, scope func() string) *Service {
	return &Service{repo: repo, refresh: refresh, provider: provider, scope: scope, entries: map[string]*entry{}, slots: make(chan struct{}, 4)}
}
func (s *Service) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}
func (s *Service) fingerprint(d credential.Data) [32]byte {
	return sha256.Sum256([]byte(s.scope() + "\x00" + d.Text("bearer_token") + "\x00" + d.Text("domain") + "\x00" + d.Text("user_id") + "\x00" + d.Text("enterprise_id") + "\x00" + d.Text("enterpriseId")))
}
func (s *Service) getEntry(name string) *entry {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e := s.entries[name]; e != nil {
		return e
	}
	e := &entry{gate: make(chan struct{}, 1)}
	s.entries[name] = e
	return e
}
func (s *Service) List(ctx context.Context) ([]Result, error) {
	records, err := s.repo.List()
	if err != nil {
		return nil, err
	}
	results := make([]Result, len(records))
	sem := make(chan struct{}, 4)
	var wg sync.WaitGroup
	// Limit both in-flight network requests and goroutine count for large pools.
	for i, r := range records {
		select {
		case sem <- struct{}{}:
		case <-ctx.Done():
			wg.Wait()
			return nil, ctx.Err()
		}
		wg.Add(1)
		go func(i int, r credential.Record) {
			defer wg.Done()
			defer func() { <-sem }()
			value, err := s.query(ctx, r.Filename, false)
			if err != nil {
				value = Result{Filename: r.Filename, Status: "error", Message: "凭证已变更，请刷新列表"}
			}
			results[i] = value
		}(i, r)
	}
	wg.Wait()
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	return results, nil
}
func (s *Service) Refresh(ctx context.Context, name string) (Result, error) {
	return s.query(ctx, name, true)
}
func (s *Service) query(ctx context.Context, name string, force bool) (Result, error) {
	// Validate the filename and account before allocating cache entries.
	if _, err := s.repo.Get(name); err != nil {
		return Result{}, err
	}
	e := s.getEntry(name)
	select {
	case e.gate <- struct{}{}:
	case <-ctx.Done():
		return Result{}, ctx.Err()
	}
	defer func() { <-e.gate }()
	d, err := s.repo.Get(name)
	if err != nil {
		return Result{}, err
	}
	key := s.fingerprint(d)
	now := s.now()
	ttl := 5 * time.Minute
	if e.result.Status != "ok" {
		ttl = time.Minute
	}
	age := now.Sub(e.checked)
	// Even explicit refreshes are coalesced for ten seconds.
	valid := key == e.fingerprint && !e.checked.IsZero() && age >= 0 && ((!force && age < ttl) || age < 10*time.Second)
	if valid && e.result.Balance != nil && !e.result.Balance.refreshAt.IsZero() && !now.Before(e.result.Balance.refreshAt) {
		valid = false
	}
	if valid && e.checked.In(china).Format("2006-01-02") != now.In(china).Format("2006-01-02") {
		valid = false
	}
	if valid && e.result.Balance != nil && e.result.Balance.NextExpiryAt != nil && now.Unix() >= *e.result.Balance.NextExpiryAt {
		valid = false
	}
	if valid {
		return e.result, nil
	}
	queryCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	select {
	case s.slots <- struct{}{}:
	case <-queryCtx.Done():
		return Result{}, queryCtx.Err()
	}
	defer func() { <-s.slots }()
	selection, err := s.refresh.Ensure(queryCtx, credential.Selection{Record: credential.Record{Filename: name, Data: d}}, false)
	var balance Balance
	if err == nil {
		balance, err = s.provider.Query(queryCtx, selection.Data)
		var f *fault.Error
		if errors.As(err, &f) && f.Status == 401 && selection.Data.Text("refresh_token") != "" {
			selection, err = s.refresh.Ensure(queryCtx, selection, true)
			if err == nil {
				balance, err = s.provider.Query(queryCtx, selection.Data)
			}
		}
	}
	if ctx.Err() != nil {
		return Result{}, ctx.Err()
	}
	current, readErr := s.repo.Get(name)
	if readErr != nil {
		return Result{}, readErr
	}
	used := selection.Data
	if used == nil {
		used = d
	}
	if s.fingerprint(current) != s.fingerprint(used) {
		return Result{}, credential.ErrConflict
	}
	result := Result{Filename: name, Status: "ok", CheckedAt: s.now().Unix()}
	if err == nil {
		result.Balance = &balance
		at := result.CheckedAt
		result.UpdatedAt = &at
	} else {
		result.Status = "error"
		result.Message = "额度查询暂时失败，请稍后刷新"
		var f *fault.Error
		if errors.As(err, &f) {
			switch f.Status {
			case 401:
				result.Status = "unauthorized"
				result.Message = "登录已失效，请重新授权后查询额度"
			case 403:
				result.Status = "forbidden"
				result.Message = "该凭证暂时无法查询额度"
			case 404:
				result.Status = "unsupported"
				result.Message = "当前站点暂不支持额度查询"
			}
		}
		// Keep the last successful value with a visible stale flag; never turn errors
		// into zero credits, or carry a balance across account/endpoint changes.
		if key == e.fingerprint && e.result.Balance != nil {
			result.Balance = e.result.Balance
			result.UpdatedAt = e.result.UpdatedAt
			result.Stale = true
		}
	}
	e.result = result
	e.fingerprint = s.fingerprint(current)
	e.checked = s.now()
	return result, nil
}
