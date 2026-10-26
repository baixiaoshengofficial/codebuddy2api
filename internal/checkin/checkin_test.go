package checkin

import (
	"context"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
)

type settings struct {
	enabled  bool
	schedule string
}

func (s settings) Enabled() bool     { return s.enabled }
func (s settings) Schedule() string  { return s.schedule }
func (s settings) Get(string) string { return "" }

type refresh struct{ calls int }

func (r *refresh) Ensure(_ context.Context, s credential.Selection, force bool) (credential.Selection, error) {
	r.calls++
	return s, nil
}

type billing struct {
	status         Data
	err            error
	checks, claims int
}

func (b *billing) Status(context.Context, Data) (Data, error) { b.checks++; return b.status, b.err }
func (b *billing) Claim(context.Context, Data) (Data, error) {
	b.claims++
	return Data{"credit": 10, "today_checked_in": true}, nil
}
func newService(t *testing.T, b *billing) (*Service, *refresh) {
	t.Helper()
	root := t.TempDir()
	repo := &credential.JSONRepository{Directory: filepath.Join(root, "accounts")}
	repo.Put("account.json", Data{"bearer_token": "access", "user_id": "user", "created_at": time.Date(2026, 10, 26, 11, 0, 0, 0, China).Unix(), "expires_in": 86400})
	r := &refresh{}
	s, err := New(repo, r, b, settings{true, "11:00"}, JSONState{Path: filepath.Join(root, "checkin.json")})
	if err != nil {
		t.Fatal(err)
	}
	s.Now = func() time.Time { return time.Date(2026, 10, 26, 12, 0, 0, 0, China) }
	return s, r
}
func TestCheckinOutcomesAndNoDuplicateClaim(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status Data
		err    error
		want   string
		claims int
	}{{"claimed", Data{"active": true}, nil, "claimed", 1}, {"already claimed", Data{"active": true, "today_checked_in": true}, nil, "already_checked_in", 0}, {"inactive", Data{"active": false}, nil, "inactive", 0}, {"expired", nil, fault.New(401, "expired", "expired"), "expired", 0}, {"business error", nil, fault.New(400, "error", "error"), "error", 0}} {
		t.Run(tc.name, func(t *testing.T) {
			b := &billing{status: tc.status, err: tc.err}
			s, _ := newService(t, b)
			result, err := s.Run(context.Background(), false, true)
			if err != nil {
				t.Fatal(err)
			}
			accounts := result["accounts"].([]Data)
			if len(accounts) != 1 || accounts[0].Text("status") != tc.want || b.claims != tc.claims {
				t.Fatal(accounts, b.claims)
			}
			if tc.want == "claimed" {
				if _, err := s.Run(context.Background(), false, true); err != nil {
					t.Fatal(err)
				}
				if b.claims != 1 {
					t.Fatal("claimed twice")
				}
			}
			restarted, err := New(s.Repo, s.Refresh, b, s.Settings, s.Persistence)
			if err != nil {
				t.Fatal(err)
			}
			restarted.Now = s.Now
			status, err := restarted.Status()
			if err != nil || status["last_scheduled_date"] != "2026-10-26" {
				t.Fatal(status, err)
			}
		})
	}
}
func TestScheduleAndStatusReadOnly(t *testing.T) {
	s, _ := newService(t, &billing{})
	beforeRows, _ := s.Repo.List()
	before := beforeRows[0].Data
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); s.Status() }()
	}
	wg.Wait()
	afterRows, _ := s.Repo.List()
	after := afterRows[0].Data
	if before.Text("bearer_token") != after.Text("bearer_token") {
		t.Fatal("read mutated credential")
	}
	if !s.next(Data{}).Equal(s.now()) {
		t.Fatal("missed schedule not caught up")
	}
	target := s.next(Data{"last_scheduled_date": "2026-10-26"})
	if target.Day() != 27 || target.Hour() != 11 {
		t.Fatal(target)
	}
	retry := s.now().Add(5 * time.Minute)
	if got := s.next(Data{"last_scheduled_date": "2026-10-26", "scheduled_retry_at": retry.Unix()}); !got.Equal(retry) {
		t.Fatal(got)
	}
}

type doer func(*http.Request) (*http.Response, error)

func (f doer) Do(r *http.Request) (*http.Response, error) { return f(r) }
func TestBillingHeadersAndBusinessResponses(t *testing.T) {
	for _, tc := range []struct {
		body        string
		http        int
		claim, fail bool
	}{{`{"code":0,"data":{"active":true}}`, 200, false, false}, {`{"code":10001,"msg":"already"}`, 400, true, false}, {`{"code":9,"msg":"failure"}`, 400, true, true}, {`{}`, 401, false, true}, {`{}`, 500, false, true}, {`{"data":{"active":true}}`, 200, false, true}} {
		t.Run(tc.body, func(t *testing.T) {
			p := Provider{Endpoint: func() string { return "https://site.invalid" }, Client: doer(func(r *http.Request) (*http.Response, error) {
				if r.Header.Get("X-User-Id") != "uid" || r.Header.Get("X-Tenant-Id") != "tenant" || r.Header.Get("Authorization") != "Bearer access" {
					t.Error(r.Header)
				}
				return &http.Response{StatusCode: tc.http, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(tc.body))}, nil
			})}
			d := Data{"user_id": "uid", "bearer_token": "access", "enterprise_id": "tenant"}
			var err error
			if tc.claim {
				_, err = p.Claim(context.Background(), d)
			} else {
				_, err = p.Status(context.Background(), d)
			}
			if (err != nil) != tc.fail {
				t.Fatal(err)
			}
		})
	}
}
