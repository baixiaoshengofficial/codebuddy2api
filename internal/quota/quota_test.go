package quota

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
)

var testNow = time.Date(2026, 11, 4, 12, 0, 0, 0, china)

func resource(id, left, size string) credential.Data {
	return credential.Data{"AccountId": id, "CapacityUnit": "credits", "CapacityType": 1, "Status": 0, "CycleCapacitySizePrecise": size, "CycleCapacityRemainPrecise": left, "CycleStartTime": "2026-11-01 00:00:00", "CycleEndTime": "2026-12-01 00:00:00"}
}
func TestNormalizeBalances(t *testing.T) {
	precise := resource("1", "20.28000018", "100")
	precise["CycleCapacityRemain"] = 20
	used := resource("2", "0", "100")
	used["Status"] = 3
	expired := resource("3", "100", "100")
	expired["DeductionEndTime"] = testNow.Add(-time.Hour).UnixMilli()
	future := resource("4", "100", "100")
	future["CycleStartTime"] = "2026-12-01 00:00:00"
	tokens := resource("5", "1000", "1000")
	tokens["CapacityUnit"] = "tokens"
	cancelled := resource("6", "100", "100")
	cancelled["Status"] = 2
	slice := resource("7", "500", "500")
	slice["CapacityType"] = 4
	slice["SlicePeriodUsageDetails"] = []any{map[string]any{"SlicePeriodStartTime": "2026-11-04 00:00:00", "SlicePeriodEndTime": "2026-11-04 23:59:59", "SlicePeriodCapacitySizePrecise": "50", "SlicePeriodCapacityRemainPrecise": "12.5"}}
	balance, err := normalize([]credential.Data{precise, used, expired, future, tokens, cancelled, slice}, testNow)
	if err != nil {
		t.Fatal(err)
	}
	if balance.Remaining != "32.78000018" || balance.Total != "250" || balance.Used != "217.21999982" || balance.Packages != 3 || balance.ExpiringCredits != "12.5" {
		t.Fatalf("%+v", balance)
	}
	want, _ := time.ParseInLocation("2006-01-02 15:04:05", "2026-11-04 23:59:59", china)
	if balance.NextExpiryAt == nil || *balance.NextExpiryAt != want.Unix() {
		t.Fatal(balance)
	}
}
func TestNormalizeNeverInventsZero(t *testing.T) {
	for _, change := range []func(credential.Data){
		func(d credential.Data) { delete(d, "CycleCapacityRemainPrecise") },
		func(d credential.Data) { d["CycleCapacityRemainPrecise"] = "not-a-number" },
		func(d credential.Data) { d["CycleCapacityRemainPrecise"] = "101" },
		func(d credential.Data) { d["CycleCapacityRemainPrecise"] = "-1" },
		func(d credential.Data) { d["CycleCapacityRemainPrecise"] = "NaN" },
		func(d credential.Data) { d["CycleStartTime"] = "bad" },
		func(d credential.Data) { delete(d, "Status") },
		func(d credential.Data) { d["CapacityType"] = 4 },
	} {
		d := resource("1", "10", "100")
		change(d)
		if _, err := normalize([]credential.Data{d}, testNow); err == nil {
			t.Fatal("accepted invalid credits", d)
		}
	}
	empty, err := normalize(nil, testNow)
	if err != nil || empty.Remaining != "0" || empty.Total != "0" || empty.NextExpiryAt != nil {
		t.Fatal(empty, err)
	}
}

type doer func(*http.Request) (*http.Response, error)

func (f doer) Do(r *http.Request) (*http.Response, error) { return f(r) }
func reply(status int, value any) *http.Response {
	body, _ := json.Marshal(value)
	return &http.Response{StatusCode: status, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(string(body)))}
}
func envelope(rows []credential.Data, total int) credential.Data {
	return credential.Data{"code": 0, "data": credential.Data{"Response": credential.Data{"Data": credential.Data{"Accounts": rows, "TotalCount": total}}}}
}
func TestProviderPaginationAndHeaders(t *testing.T) {
	calls := 0
	p := Provider{Endpoint: func() string { return "https://official.invalid" }, Agent: "WorkBuddy/test", Now: func() time.Time { return testNow }, Client: doer(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.Method != "POST" || r.URL.Path != "/v2/billing/meter/get-user-resource" || r.Header.Get("Authorization") != "Bearer secret" || r.Header.Get("X-Product") != "SaaS" || r.Header.Get("X-Domain") != "login.invalid" || r.Header.Get("X-User-Id") != "uid" || r.Header.Get("X-Tenant-Id") != "tenant" {
			t.Error("wrong quota request headers or URL")
		}
		var body credential.Data
		json.NewDecoder(r.Body).Decode(&body)
		if body.Number("PageNumber") != int64(calls) || body.Text("SlicePeriodStartTime") != "2026-11-04 00:00:00" || body.Text("SlicePeriodEndTime") != "2026-11-04 23:59:59" || body["OnlyValidPeriod"] != true {
			t.Error(body)
		}
		if calls == 1 {
			return reply(200, envelope([]credential.Data{resource("1", "0.1", "1")}, 2)), nil
		}
		return reply(200, envelope([]credential.Data{resource("2", "0.2", "1")}, 2)), nil
	})}
	balance, err := p.Query(context.Background(), credential.Data{"bearer_token": "secret", "domain": "login.invalid", "user_id": "uid", "enterpriseId": "tenant"})
	if err != nil || calls != 2 || balance.Remaining != "0.3" {
		t.Fatal(balance, err, calls)
	}
}
func TestProviderRejectsErrorsPartialPagesAndSecretEchoes(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
		body   any
	}{
		{"auth", 401, credential.Data{"msg": "secret"}},
		{"permission", 403, credential.Data{"msg": "secret"}},
		{"unsupported", 404, credential.Data{}},
		{"busy", 429, credential.Data{}},
		{"business error", 200, credential.Data{"code": 10085, "msg": "secret"}},
		{"missing schema", 200, credential.Data{"code": 0, "data": credential.Data{}}},
		{"partial pagination", 200, envelope([]credential.Data{}, 5)},
		{"repeated pagination", 200, envelope([]credential.Data{resource("1", "10", "100")}, 3)},
		{"truncated list", 200, envelope([]credential.Data{resource("1", "10", "100")}, 0)},
		{"huge list", 200, envelope([]credential.Data{}, 10001)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			p := Provider{Endpoint: func() string { return "https://official.invalid" }, Now: func() time.Time { return testNow }, Client: doer(func(*http.Request) (*http.Response, error) { calls++; return reply(tc.status, tc.body), nil })}
			_, err := p.Query(context.Background(), credential.Data{})
			if err == nil || strings.Contains(err.Error(), "secret") || calls > 2 {
				t.Fatal(err, calls)
			}
		})
	}
}

type memoryRepo struct {
	mu   sync.Mutex
	data credential.Data
}

func (r *memoryRepo) Get(name string) (credential.Data, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if name != "a.json" {
		return nil, credential.ErrNotFound
	}
	return credential.Clone(r.data), nil
}
func (r *memoryRepo) List() ([]credential.Record, error) {
	d, _ := r.Get("a.json")
	return []credential.Record{{Filename: "a.json", Data: d}}, nil
}

type refreshFunc func(context.Context, credential.Selection, bool) (credential.Selection, error)

func (f refreshFunc) Ensure(c context.Context, s credential.Selection, b bool) (credential.Selection, error) {
	return f(c, s, b)
}

type queryFunc func(context.Context, credential.Data) (Balance, error)

func (f queryFunc) Query(c context.Context, d credential.Data) (Balance, error) { return f(c, d) }
func service(t *testing.T, reader Reader) (*Service, *memoryRepo, *time.Time) {
	t.Helper()
	repo := &memoryRepo{data: credential.Data{"bearer_token": "secret", "refresh_token": "refresh"}}
	now := testNow
	s := New(repo, refreshFunc(func(_ context.Context, s credential.Selection, _ bool) (credential.Selection, error) { return s, nil }), reader, func() string { return "https://official.invalid" })
	s.Now = func() time.Time { return now }
	return s, repo, &now
}
func TestServiceCacheCoalescesConcurrentRefreshes(t *testing.T) {
	var calls atomic.Int32
	started, finish := make(chan struct{}), make(chan struct{})
	s, _, _ := service(t, queryFunc(func(context.Context, credential.Data) (Balance, error) {
		if calls.Add(1) == 1 {
			close(started)
		}
		<-finish
		return Balance{Remaining: "10", Total: "100", Used: "90"}, nil
	}))
	var wg sync.WaitGroup
	for i := 0; i < 15; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			result, err := s.Refresh(context.Background(), "a.json")
			if err != nil || result.Status != "ok" {
				t.Error(result, err)
			}
		}()
	}
	<-started
	close(finish)
	wg.Wait()
	results, err := s.List(context.Background())
	if err != nil || len(results) != 1 || calls.Load() != 1 {
		t.Fatal(results, err, calls.Load())
	}
}
func TestServiceStaleFailureInvalidationAndTTL(t *testing.T) {
	calls := 0
	fail := false
	s, repo, now := service(t, queryFunc(func(context.Context, credential.Data) (Balance, error) {
		calls++
		if fail {
			return Balance{}, errors.New("secret upstream body")
		}
		return Balance{Remaining: "10", Total: "100", Used: "90"}, nil
	}))
	first, _ := s.Refresh(context.Background(), "a.json")
	fail = true
	*now = now.Add(30 * time.Second)
	cached, err := s.List(context.Background())
	if err != nil || calls != 1 || cached[0].Status != "ok" {
		t.Fatal(cached, err, calls)
	}
	stale, _ := s.Refresh(context.Background(), "a.json")
	if !stale.Stale || stale.Balance.Remaining != "10" || stale.Status != "error" || strings.Contains(stale.Message, "secret") || *stale.UpdatedAt != *first.UpdatedAt {
		t.Fatal(stale)
	}
	*now = now.Add(20 * time.Second)
	s.List(context.Background())
	if calls != 2 {
		t.Fatal("error cache ignored")
	}
	repo.mu.Lock()
	repo.data["bearer_token"] = "replacement"
	repo.mu.Unlock()
	changed, _ := s.List(context.Background())
	if changed[0].Balance != nil || changed[0].Stale {
		t.Fatal("old account balance survived token change")
	}
	fail = false
	*now = now.Add(61 * time.Second)
	fresh, _ := s.List(context.Background())
	if fresh[0].Status != "ok" || calls != 4 {
		t.Fatal(fresh, calls)
	}
}
func TestServiceRenewsOnlyUnauthorizedReads(t *testing.T) {
	for _, status := range []int{401, 403, 503} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			calls, forced := 0, 0
			s, repo, _ := service(t, queryFunc(func(_ context.Context, d credential.Data) (Balance, error) {
				calls++
				if calls == 1 {
					return Balance{}, fault.New(status, "upstream", "secret")
				}
				if d.Text("bearer_token") != "renewed" {
					t.Error("old token reused")
				}
				return Balance{Remaining: "1"}, nil
			}))
			s.refresh = refreshFunc(func(_ context.Context, selected credential.Selection, force bool) (credential.Selection, error) {
				if force {
					forced++
					repo.mu.Lock()
					repo.data["bearer_token"] = "renewed"
					repo.mu.Unlock()
					selected.Data, _ = repo.Get("a.json")
				}
				return selected, nil
			})
			result, err := s.Refresh(context.Background(), "a.json")
			if err != nil {
				t.Fatal(err)
			}
			if status == 401 {
				if forced != 1 || calls != 2 || result.Status != "ok" {
					t.Fatal(result, calls, forced)
				}
			} else if forced != 0 || calls != 1 || result.Balance != nil {
				t.Fatal(result, calls, forced)
			}
		})
	}
}
func TestServiceExpiryAndCancellation(t *testing.T) {
	calls := 0
	s, _, now := service(t, queryFunc(func(context.Context, credential.Data) (Balance, error) {
		calls++
		expiry := testNow.Add(time.Second).Unix()
		return Balance{Remaining: "1", NextExpiryAt: &expiry}, nil
	}))
	s.Refresh(context.Background(), "a.json")
	*now = now.Add(2 * time.Second)
	s.List(context.Background())
	if calls != 2 {
		t.Fatal("expired balance reused")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := s.Refresh(ctx, "a.json"); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if _, err := s.Refresh(context.Background(), "../secret.json"); !errors.Is(err, credential.ErrNotFound) {
		t.Fatal(err)
	}
}

type poolRepo struct{ records []credential.Record }

func (r poolRepo) List() ([]credential.Record, error) { return r.records, nil }
func (r poolRepo) Get(name string) (credential.Data, error) {
	for _, record := range r.records {
		if record.Filename == name {
			return credential.Clone(record.Data), nil
		}
	}
	return nil, credential.ErrNotFound
}
func TestServiceLimitsQueriesAcrossConcurrentClients(t *testing.T) {
	records := []credential.Record{}
	for i := 0; i < 12; i++ {
		records = append(records, credential.Record{Filename: fmt.Sprintf("%d.json", i), Data: credential.Data{"bearer_token": "secret"}})
	}
	var active, max atomic.Int32
	started := make(chan struct{}, 12)
	provider := queryFunc(func(ctx context.Context, _ credential.Data) (Balance, error) {
		n := active.Add(1)
		defer active.Add(-1)
		for old := max.Load(); n > old; old = max.Load() {
			if max.CompareAndSwap(old, n) {
				break
			}
		}
		started <- struct{}{}
		<-ctx.Done()
		return Balance{}, ctx.Err()
	})
	s := New(poolRepo{records}, refreshFunc(func(_ context.Context, s credential.Selection, _ bool) (credential.Selection, error) { return s, nil }), provider, func() string { return "site" })
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var wg sync.WaitGroup
	for _, r := range records {
		wg.Add(1)
		go func(name string) { defer wg.Done(); s.Refresh(ctx, name) }(r.Filename)
	}
	for i := 0; i < 4; i++ {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("quota workers stalled")
		}
	}
	cancel()
	wg.Wait()
	if max.Load() > 4 {
		t.Fatal("query concurrency limit exceeded", max.Load())
	}
}
func TestServiceRejectsAccountReplacementDuringQuery(t *testing.T) {
	started, finish := make(chan struct{}), make(chan struct{})
	s, repo, _ := service(t, queryFunc(func(context.Context, credential.Data) (Balance, error) {
		close(started)
		<-finish
		return Balance{Remaining: "100"}, nil
	}))
	done := make(chan error, 1)
	go func() { _, err := s.Refresh(context.Background(), "a.json"); done <- err }()
	<-started
	repo.mu.Lock()
	repo.data["bearer_token"] = "new-account"
	repo.mu.Unlock()
	close(finish)
	if err := <-done; !errors.Is(err, credential.ErrConflict) {
		t.Fatal("old account query was accepted", err)
	}
}

func TestEmptyBalanceExpiresAtResetAndSiteChangesInvalidateCache(t *testing.T) {
	calls := 0
	s, _, now := service(t, queryFunc(func(context.Context, credential.Data) (Balance, error) {
		calls++
		return Balance{Remaining: "0", refreshAt: testNow.Add(time.Minute)}, nil
	}))
	s.Refresh(context.Background(), "a.json")
	*now = now.Add(time.Minute)
	s.List(context.Background())
	if calls != 2 {
		t.Fatal("zero balance survived reset")
	}
	s.scope = func() string { return "another-site" }
	s.List(context.Background())
	if calls != 3 {
		t.Fatal("balance survived site change")
	}
}

func TestServiceRejectsSiteChangeDuringQuery(t *testing.T) {
	site := "first-site"
	s, _, _ := service(t, queryFunc(func(context.Context, credential.Data) (Balance, error) {
		site = "second-site"
		return Balance{Remaining: "100"}, nil
	}))
	s.scope = func() string { return site }
	if _, err := s.Refresh(context.Background(), "a.json"); !errors.Is(err, credential.ErrConflict) {
		t.Fatal("cached a response from the previous site", err)
	}
}
