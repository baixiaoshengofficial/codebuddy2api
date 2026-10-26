package oauth

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
)

type doer func(*http.Request) (*http.Response, error)

func (f doer) Do(r *http.Request) (*http.Response, error) { return f(r) }
func response(status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(body)), Header: http.Header{}}
}
func TestProductProtocol(t *testing.T) {
	for _, product := range []Product{CodeBuddy, WorkBuddy} {
		t.Run(product.Name, func(t *testing.T) {
			calls := 0
			c := Client{Product: product, Endpoint: func() string { return "https://example.invalid" }}
			c.HTTP = doer(func(r *http.Request) (*http.Response, error) {
				calls++
				if r.Header.Get("X-Domain") != "example.invalid" {
					t.Error(r.Header)
				}
				switch r.URL.Path {
				case "/v2/plugin/auth/state":
					var body Data
					json.NewDecoder(r.Body).Decode(&body)
					if r.Method != "POST" || r.URL.Query().Get("platform") != product.Platform || body.Text("nonce") == "" || body.Text("nonce") != r.URL.Query().Get("nonce") {
						t.Error("wrong authorization request")
					}
					return response(200, `{"code":0,"data":{"state":"s&unsafe=1","authUrl":"https://login.invalid"}}`), nil
				case "/v2/plugin/auth/token":
					if r.URL.Query().Get("state") != "s&unsafe=1" || r.URL.Query().Get("unsafe") != "" || r.Method != "GET" {
						t.Error(r.URL)
					}
					if (r.Header.Get("Content-Type") != "") != product.PollContentType {
						t.Error("wrong product content type")
					}
					return response(200, `{"code":0,"data":{"accessToken":"access","refreshToken":"refresh","domain":"example.invalid"}}`), nil
				case "/v2/plugin/login/account":
					if r.Header.Get("Authorization") != "Bearer access" || r.Header.Get("X-Product") != "WorkBuddy" {
						t.Error("account headers")
					}
					return response(200, `{"data":{"uid":"top","phone":"123","account":{"uid":"nested","email":"mail@example.com"}}}`), nil
				}
				return nil, errors.New("unexpected path")
			})
			started, err := c.Start(context.Background())
			if err != nil || started.Text("auth_state") != "s&unsafe=1" || started.Number("expires_in") != int64(product.Expiry) {
				t.Fatal(started, err)
			}
			token, pending, err := c.Poll(context.Background(), started.Text("auth_state"))
			if err != nil || pending || token.Text("access_token") != "access" {
				t.Fatal(token, pending, err)
			}
			if product.Name == "workbuddy" && (token.Text("account_uid") != "top" || token.Text("account_email") != "mail@example.com" || calls != 3) {
				t.Fatal(token, calls)
			}
		})
	}
}
func TestStartDuplicateStateRetriesOnce(t *testing.T) {
	calls := 0
	c := Client{Product: CodeBuddy, Endpoint: func() string { return "https://test.invalid" }, HTTP: doer(func(*http.Request) (*http.Response, error) {
		calls++
		return response(200, `{"code":0,"data":{"state":"same","authUrl":"https://login.invalid"}}`), nil
	})}
	if _, err := c.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Start(context.Background()); err == nil || calls != 3 {
		t.Fatal(calls, err)
	}
}
func TestMalformedAndPendingOutcomes(t *testing.T) {
	for _, body := range []string{`[]`, `{}`, `{"code":0,"data":[]}`, `{"code":0,"data":{"accessToken":123}}`, `{"code":0,"data":{"accessToken":""}}`} {
		t.Run(body, func(t *testing.T) {
			c := Client{Product: CodeBuddy, Endpoint: func() string { return "https://test.invalid" }, HTTP: doer(func(*http.Request) (*http.Response, error) { return response(200, body), nil })}
			if _, err := c.Start(context.Background()); err == nil {
				t.Fatal("bad start accepted")
			}
			if _, _, err := c.Poll(context.Background(), "state"); err == nil {
				t.Fatal("bad token accepted")
			}
		})
	}
	c := Client{Product: CodeBuddy, Endpoint: func() string { return "https://test.invalid" }, HTTP: doer(func(*http.Request) (*http.Response, error) { return response(200, `{"code":11217}`), nil })}
	_, pending, err := c.Poll(context.Background(), "state")
	if err != nil || !pending {
		t.Fatal(pending, err)
	}
}

type provider struct {
	starts, polls atomic.Int32
	token         Data
	pending       bool
}

func (p *provider) Start(context.Context) (Data, error) {
	n := p.starts.Add(1)
	return Data{"success": true, "auth_state": string(rune('a' + n)), "verification_uri_complete": "https://login.invalid", "expires_in": 900}, nil
}
func (p *provider) Poll(context.Context, string) (Data, bool, error) {
	p.polls.Add(1)
	return p.token, p.pending, nil
}
func jwt(email string) string {
	b, _ := json.Marshal(Data{"email": email, "sub": email})
	return "header." + base64.RawURLEncoding.EncodeToString(b) + ".sig"
}
func authService(t *testing.T) (*Service, *provider, *credential.JSONRepository) {
	t.Helper()
	repo := &credential.JSONRepository{Directory: filepath.Join(t.TempDir(), "creds")}
	p := &provider{token: Data{"access_token": jwt("one@example.com"), "domain": "site"}}
	s := &Service{Provider: p, Store: repo, Accounts: &credential.Accounts{Repo: repo, Product: "codebuddy"}, Product: "codebuddy"}
	return s, p, repo
}
func TestAuthorizationIdempotencyAndReauthorization(t *testing.T) {
	s, p, repo := authService(t)
	started, err := s.Start(context.Background(), "")
	if err != nil {
		t.Fatal(err)
	}
	state := started.Text("auth_state")
	p.pending = true
	if _, err := s.Poll(context.Background(), state, false); err == nil {
		t.Fatal("pending accepted")
	}
	p.pending = false
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			result, err := s.Poll(context.Background(), state, false)
			if err != nil || result["saved"] != true || result["access_token"] != nil {
				t.Error(result, err)
			}
		}()
	}
	wg.Wait()
	if p.polls.Load() != 2 {
		t.Fatal("poll replayed", p.polls.Load())
	}
	records, _ := repo.List()
	if len(records) != 1 {
		t.Fatal(records)
	}
	name := records[0].Filename
	old := records[0].Data
	old["full_response"] = Data{"accessToken": "old"}
	old["session_dead_at"] = 1
	repo.Put(name, old)
	reauth, err := s.Start(context.Background(), name)
	if err != nil {
		t.Fatal(err)
	}
	p.token = Data{"access_token": jwt("other@example.com"), "domain": "site"}
	_, err = s.Poll(context.Background(), reauth.Text("auth_state"), true)
	var f *fault.Error
	if !errors.As(err, &f) || f.Status != 409 {
		t.Fatal(err)
	}
	stored, _ := repo.Get(name)
	if stored.Text("bearer_token") != old.Text("bearer_token") {
		t.Fatal("wrong account changed original")
	}
	p.token = Data{"access_token": jwt("one@example.com"), "domain": "site", "refresh_token": "fresh"}
	result, err := s.Poll(context.Background(), reauth.Text("auth_state"), true)
	if err != nil || result.Text("filename") != name {
		t.Fatal(result, err)
	}
	records, _ = repo.List()
	if len(records) != 1 || records[0].Data["full_response"] != nil || records[0].Data["session_dead_at"] != nil {
		t.Fatal(records)
	}
}
func TestSessionExpiryDeletionAndConcurrentUpdate(t *testing.T) {
	for _, scenario := range []string{"expired", "deleted", "updated", "wrong poll type", "unknown"} {
		t.Run(scenario, func(t *testing.T) {
			s, _, repo := authService(t)
			original := credential.Build("codebuddy", Data{"access_token": jwt("one@example.com")}, time.Now())
			repo.Put("old.json", original)
			clock := time.Now()
			s.Now = func() time.Time { return clock }
			start, err := s.Start(context.Background(), "old.json")
			if err != nil {
				t.Fatal(err)
			}
			state := start.Text("auth_state")
			reauth := true
			switch scenario {
			case "expired":
				clock = clock.Add(time.Hour)
			case "deleted":
				repo.Delete("old.json")
			case "updated":
				repo.Put("old.json", Data{"bearer_token": "different"})
			case "wrong poll type":
				reauth = false
			case "unknown":
				state = "unknown"
			}
			if _, err := s.Poll(context.Background(), state, reauth); err == nil {
				t.Fatal("conflict accepted")
			}
		})
	}
}

func TestSavedLoginIsNotRepolledAfterRoutingSaveFailure(t *testing.T) {
	s, p, repo := authService(t)
	hookCalls := 0
	s.OnSaved = func(filename string) error {
		hookCalls++
		if filename == "" {
			t.Fatal("missing saved filename")
		}
		if hookCalls == 1 {
			return errors.New("disk unavailable")
		}
		return nil
	}
	start, err := s.Start(context.Background(), "")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Poll(context.Background(), start.Text("auth_state"), false); err == nil {
		t.Fatal("missing storage error")
	}
	result, err := s.Poll(context.Background(), start.Text("auth_state"), false)
	if err != nil || result["saved"] != true || p.polls.Load() != 1 || hookCalls != 2 {
		t.Fatal(result, err, p.polls.Load(), hookCalls)
	}
	records, _ := repo.List()
	if len(records) != 1 {
		t.Fatal("duplicate saved credential")
	}
}
