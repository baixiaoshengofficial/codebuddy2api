package credential

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
)

var testNow = time.Unix(1800000000, 0)

func testRepo(t *testing.T) *JSONRepository {
	t.Helper()
	return &JSONRepository{Directory: filepath.Join(t.TempDir(), "accounts")}
}
func put(t *testing.T, r Repository, name string, d Data) {
	t.Helper()
	if err := r.Put(name, d); err != nil {
		t.Fatal(err)
	}
}
func tokenFor(claims Data) string {
	b, _ := json.Marshal(claims)
	return "eyJhbGciOiJub25lIn0." + base64.RawURLEncoding.EncodeToString(b) + ".signature"
}
func TestStorageAndAtomicUpdates(t *testing.T) {
	repo := testRepo(t)
	records, err := repo.List()
	if err != nil || len(records) != 0 {
		t.Fatal(records, err)
	}
	if _, err := os.Stat(repo.Directory); !os.IsNotExist(err) {
		t.Fatal("read created directory")
	}
	put(t, repo, "a.json", Data{"bearer_token": "old", "custom": "keep"})
	if err := repo.CompareAndSwap("a.json", "wrong", Data{"bearer_token": "bad"}); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	if err := repo.Put("a.json", Data{"bearer_token": "new", "invalid": make(chan int)}); err == nil {
		t.Fatal("expected serialization failure")
	}
	old, _ := repo.Get("a.json")
	if old.Text("bearer_token") != "old" {
		t.Fatal("failed update overwrote token")
	}
	if err := repo.CompareAndSwap("a.json", "old", Data{"bearer_token": "new", "custom": "keep"}); err != nil {
		t.Fatal(err)
	}
	mode, _ := os.Stat(filepath.Join(repo.Directory, "a.json"))
	if mode.Mode().Perm() != 0600 {
		t.Fatal(mode.Mode())
	}
	for _, name := range []string{"../a.json", "a", "manager_state.json", "/tmp/escape.json", `..\a.json`} {
		t.Run(name, func(t *testing.T) {
			if err := repo.Put(name, Data{"bearer_token": "secret"}); err == nil {
				t.Fatal("accepted invalid path")
			}
		})
	}
	for name, content := range map[string]string{"broken.json": "{", "array.json": "[]", "incomplete.json": "{}", "manager_state.json": "{}"} {
		os.WriteFile(filepath.Join(repo.Directory, name), []byte(content), 0600)
	}
	records, err = repo.List()
	if err != nil || len(records) != 1 {
		t.Fatal(records, err)
	}
	files, _ := os.ReadDir(repo.Directory)
	for _, f := range files {
		if strings.HasPrefix(f.Name(), ".write-") {
			t.Fatal("temporary file leaked")
		}
	}
	if err := repo.Delete("a.json"); err != nil {
		t.Fatal(err)
	}
	if _, err := repo.Get("a.json"); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
}
func newTestRouter(t *testing.T, repo Repository, now *time.Time) *Router {
	t.Helper()
	r, err := NewRouter(repo, JSONState{Path: filepath.Join(t.TempDir(), "manager_state.json")}, func() time.Time { return *now })
	if err != nil {
		t.Fatal(err)
	}
	return r
}
func TestStickyFailoverManualAndRestart(t *testing.T) {
	repo := testRepo(t)
	for _, name := range []string{"a.json", "b.json", "c.json"} {
		put(t, repo, name, Data{"bearer_token": name, "user_id": name})
	}
	now := testNow
	r := newTestRouter(t, repo, &now)
	for i := 0; i < 10; i++ {
		selected, err := r.Select("", nil)
		if err != nil || selected.Filename != "a.json" {
			t.Fatal(selected, err)
		}
	}
	before, after, err := r.Exhausted("a.json")
	if err != nil || before != "a.json" || after != "b.json" {
		t.Fatal(before, after, err)
	}
	selected, _ := r.Select("", nil)
	if selected.Filename != "b.json" {
		t.Fatal(selected)
	}
	snap, _ := r.Snapshot()
	if snap["current_index"] != 1 || snap["next_index"] != 2 {
		t.Fatal(snap)
	}
	restarted, err := NewRouter(repo, r.persistence, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	selected, _ = restarted.Select("", nil)
	if selected.Filename != "b.json" {
		t.Fatal("restart lost selection")
	}
	if err := restarted.SetMode("manual", "c.json"); err != nil {
		t.Fatal(err)
	}
	restarted.Exhausted("c.json")
	selected, _ = restarted.Select("", nil)
	if selected.Filename != "c.json" || selected.Auto {
		t.Fatal("manual selection changed")
	}
	explicit, _ := restarted.Select("a.json", nil)
	if explicit.Filename != "a.json" || explicit.Auto || restarted.Mode() != "manual" {
		t.Fatal("test selection mutated mode")
	}
	if err := restarted.Delete("b.json"); err != nil {
		t.Fatal(err)
	}
	selected, _ = restarted.Select("", nil)
	if selected.Filename != "c.json" {
		t.Fatal("delete changed identity")
	}
	if err := restarted.SetMode("auto", ""); err != nil {
		t.Fatal(err)
	}
	restarted.Exhausted("a.json")
	if _, err := restarted.Select("", nil); err == nil {
		t.Fatal("all exhausted should fail")
	}
	now = now.Add(301 * time.Second)
	if _, err := restarted.Select("", nil); err != nil {
		t.Fatal(err)
	}
}
func TestExpiredAndRefreshableRouting(t *testing.T) {
	now := testNow
	repo := testRepo(t)
	put(t, repo, "a.json", Data{"bearer_token": "expired", "created_at": now.Unix() - 3600, "expires_in": 600})
	put(t, repo, "b.json", Data{"bearer_token": "refreshable", "created_at": now.Unix() - 3600, "expires_in": 600, "refresh_token": "refresh"})
	r := newTestRouter(t, repo, &now)
	snap, _ := r.Snapshot()
	rows := snap["credentials"].([]Data)
	if rows[0]["state"] != "expired" || rows[1]["state"] != "refreshable" {
		t.Fatal(rows)
	}
	selected, err := r.Select("", nil)
	if err != nil || selected.Filename != "b.json" {
		t.Fatal(selected, err)
	}
}
func TestConcurrentSelectionAndExhaustion(t *testing.T) {
	now := testNow
	repo := testRepo(t)
	put(t, repo, "a.json", Data{"bearer_token": "a"})
	put(t, repo, "b.json", Data{"bearer_token": "b"})
	r := newTestRouter(t, repo, &now)
	var wg sync.WaitGroup
	for n := 0; n < 24; n++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 4; i++ {
				if _, err := r.Select("", nil); err != nil {
					t.Error(err)
				}
				r.Snapshot()
			}
		}()
	}
	wg.Wait()
	r.Exhausted("a.json")
	for i := 0; i < 10; i++ {
		selected, _ := r.Select("", nil)
		if selected.Filename != "b.json" {
			t.Fatal(selected)
		}
	}
}
func TestIdentityAndLoginReplacement(t *testing.T) {
	for _, product := range []string{"codebuddy", "workbuddy"} {
		t.Run(product, func(t *testing.T) {
			fresh := Build(product, Data{"access_token": tokenFor(Data{"email": "person@example.com", "preferred_username": "12345", "sub": "subject", "name": "Person"}), "domain": "www.codebuddy.cn", "account_uid": "uid", "phone": "12345"}, testNow)
			if fresh.Text("bearer_token") == "" || fresh.Number("created_at") != testNow.Unix() {
				t.Fatal(fresh)
			}
			want := "person@example.com"
			if product == "workbuddy" {
				want = "uid"
			}
			if fresh.Text("user_id") != want {
				t.Fatal(fresh)
			}
			old := Clone(fresh)
			old["session_dead_at"] = 123
			old["full_response"] = Data{"token": "old"}
			old["refresh_token"] = "old-refresh"
			old["custom"] = "preserved"
			replaced := LoginReplacement(old, fresh)
			if replaced["full_response"] != nil || replaced["session_dead_at"] != nil || replaced["refresh_token"] != nil || replaced["custom"] != "preserved" {
				t.Fatal(replaced)
			}
			if !SameIdentity(old, fresh) {
				t.Fatal("same account rejected")
			}
			other := Clone(fresh)
			other["domain"] = "other"
			if SameIdentity(old, other) {
				t.Fatal("cross domain accepted")
			}
			repo := testRepo(t)
			accounts := Accounts{Repo: repo, Product: product}
			name, err := accounts.Save(old)
			if err != nil {
				t.Fatal(err)
			}
			again, err := accounts.Save(fresh)
			if err != nil || name != again {
				t.Fatal(name, again, err)
			}
			records, _ := repo.List()
			if len(records) != 1 {
				t.Fatal(records)
			}
		})
	}
}

type doer func(*http.Request) (*http.Response, error)

func (f doer) Do(r *http.Request) (*http.Response, error) { return f(r) }
func response(code int, body string) *http.Response {
	return &http.Response{StatusCode: code, Body: io.NopCloser(strings.NewReader(body)), Header: http.Header{}}
}
func TestRefreshSerializedAndPersisted(t *testing.T) {
	repo := testRepo(t)
	old := Data{"bearer_token": "old", "refresh_token": "refresh", "user_id": "person", "created_at": testNow.Unix() - 3600, "expires_in": 600, "custom": "keep"}
	put(t, repo, "a.json", old)
	var calls atomic.Int32
	client := doer(func(r *http.Request) (*http.Response, error) {
		calls.Add(1)
		if r.Header.Get("X-Refresh-Token") != "refresh" || r.Header.Get("X-Auth-Refresh-Source") != "codebuddy" {
			t.Error(r.Header)
		}
		return response(200, `{"code":0,"data":{"accessToken":"new","refreshToken":"new-refresh","expiresIn":86400}}`), nil
	})
	refresh := Refresher{Repo: repo, Client: client, Product: "codebuddy", Endpoint: func() string { return "https://test.invalid" }, Now: func() time.Time { return testNow }}
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			selected, err := refresh.Ensure(context.Background(), Selection{Record: Record{"a.json", old}}, false)
			if err != nil || selected.Data.Text("bearer_token") != "new" {
				t.Error(selected, err)
			}
		}()
	}
	wg.Wait()
	if calls.Load() != 1 {
		t.Fatal("duplicate refresh", calls.Load())
	}
	stored, _ := repo.Get("a.json")
	if stored.Text("custom") != "keep" || stored.Text("refresh_token") != "new-refresh" {
		t.Fatal(stored)
	}
}
func TestRefreshFailures(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		http, want int
		dead       bool
	}{{"offline gone", `{"code":12153}`, 200, 401, true}, {"unauthorized", `{}`, 401, 401, true}, {"forbidden", `{}`, 403, 401, true}, {"server error", `{}`, 500, 503, false}, {"missing business code", `{"data":{"accessToken":"bad"}}`, 200, 503, false}, {"missing token", `{"code":0,"data":{}}`, 200, 503, false}, {"bad JSON", `[]`, 200, 503, false}} {
		t.Run(tc.name, func(t *testing.T) {
			repo := testRepo(t)
			old := Data{"bearer_token": "old", "refresh_token": "refresh", "created_at": 1, "expires_in": 600}
			put(t, repo, "a.json", old)
			r := Refresher{Repo: repo, Client: doer(func(*http.Request) (*http.Response, error) { return response(tc.http, tc.body), nil }), Endpoint: func() string { return "https://test.invalid" }, Product: "codebuddy", Now: func() time.Time { return testNow }}
			_, err := r.Ensure(context.Background(), Selection{Record: Record{"a.json", old}}, false)
			var f *fault.Error
			if !errors.As(err, &f) || f.Status != tc.want {
				t.Fatal(err)
			}
			stored, _ := repo.Get("a.json")
			if (stored.Number("session_dead_at") > 0) != tc.dead || stored.Text("bearer_token") != "old" {
				t.Fatal(stored)
			}
		})
	}
}
func TestClaimsAreDisplayOnly(t *testing.T) {
	for _, value := range []string{"", "bad", "a.b.c", "a.W10.c"} {
		if len(Claims(value)) != 0 {
			t.Fatal(value)
		}
	}
	if got := Claims(tokenFor(Data{"email": "person@example.com"})).Text("email"); got != "person@example.com" {
		t.Fatal(got)
	}
}
func TestReadinessSkipsRevokedSession(t *testing.T) {
	now := testNow
	repo := testRepo(t)
	for _, name := range []string{"a.json", "b.json"} {
		put(t, repo, name, Data{"bearer_token": name, "refresh_token": "refresh", "created_at": 1, "expires_in": 600})
	}
	router := newTestRouter(t, repo, &now)
	calls := 0
	refresh := &Refresher{Repo: repo, Client: doer(func(*http.Request) (*http.Response, error) {
		calls++
		if calls == 1 {
			return response(200, `{"code":12153}`), nil
		}
		return response(200, fmt.Sprintf(`{"code":0,"data":{"accessToken":"new","expiresIn":86400}}`)), nil
	}), Endpoint: func() string { return "https://test.invalid" }, Product: "codebuddy", Now: func() time.Time { return now }}
	selected, err := (Ready{router, refresh}).Select(context.Background(), "", nil)
	if err != nil || selected.Filename != "b.json" || calls != 2 {
		t.Fatal(selected, err, calls)
	}
}

func TestStoredNumericIdentityRoundTrip(t *testing.T) {
	repo := testRepo(t)
	put(t, repo, "uid.json", Data{"bearer_token": "token", "user_info": Data{"uid": json.Number("1234567890123456789")}})
	loaded, err := repo.Get("uid.json")
	if err != nil {
		t.Fatal(err)
	}
	if Clone(loaded).Object("user_info").Text("uid") != "1234567890123456789" {
		t.Fatal("stored identity rounded")
	}
}

func TestDecimalJSONExpiryRemainsEffective(t *testing.T) {
	d := Data{"bearer_token": "old", "created_at": json.Number("1.0"), "expires_in": json.Number("600.0")}
	if !Expired(d, testNow) {
		t.Fatal("decimal duration bypassed expiration")
	}
}
