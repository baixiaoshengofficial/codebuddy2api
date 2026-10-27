package app_test

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/app"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
)

type data = credential.Data
type doer func(*http.Request) (*http.Response, error)

func (f doer) Do(r *http.Request) (*http.Response, error) { return f(r) }
func response(status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(body))}
}
func setup(t *testing.T, client doer) (*app.App, string) {
	t.Helper()
	t.Setenv("CODEBUDDY_PASSWORD", "test-password")
	t.Setenv("CODEBUDDY_AUTO_CHECKIN", "false")
	t.Setenv("CODEBUDDY_CREDS_DIR", ".codebuddy_creds")
	t.Setenv("WORKBUDDY_CREDS_DIR", ".workbuddy_creds")
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, "frontend/dist/assets"), 0700)
	os.WriteFile(filepath.Join(root, "frontend/dist/index.html"), []byte("<!doctype html><title>admin</title>"), 0600)
	os.WriteFile(filepath.Join(root, "frontend/dist/assets/test.js"), []byte("export default 1"), 0600)
	a, err := app.New(context.Background(), app.Options{Root: root, Revision: "test-revision", Endpoint: func() string { return "https://mock.invalid" }, Client: client})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := a.Close(); err != nil {
			t.Error(err)
		}
	})
	return a, root
}
func call(a *app.App, method, path, body string, auth bool) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	if auth {
		r.Header.Set("Authorization", "Bearer test-password")
	}
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("X-Client-Name", "Codex")
	w := httptest.NewRecorder()
	a.Handler.ServeHTTP(w, r)
	return w
}
func decode(t *testing.T, w *httptest.ResponseRecorder) data {
	t.Helper()
	var result data
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	return result
}
func TestHTTPContractWithoutProviderCalls(t *testing.T) {
	var calls atomic.Int32
	a, root := setup(t, doer(func(*http.Request) (*http.Response, error) { calls.Add(1); return response(404, `{}`), nil }))
	for _, tc := range []struct {
		method, path, body string
		auth               bool
		want               int
	}{
		{"GET", "/health", "", false, 200}, {"GET", "/", "", false, 200}, {"GET", "/admin", "", false, 200}, {"GET", "/assets/test.js", "", false, 200}, {"GET", "/assets/missing.js", "", false, 404}, {"GET", "/docs", "", false, 200}, {"GET", "/openapi.json", "", false, 200},
		{"GET", "/codebuddy/v1", "", false, 200}, {"GET", "/codebuddy/v1/chat/completions", "", false, 200}, {"GET", "/codebuddy/v1/responses", "", false, 200},
		{"GET", "/api/settings", "", false, 403}, {"GET", "/api/settings", "", true, 200}, {"GET", "/api/console/status", "", true, 200}, {"GET", "/api/console/credentials", "", true, 200}, {"GET", "/workbuddy/credentials", "", true, 200}, {"GET", "/api/checkin", "", true, 200}, {"GET", "/api/stats", "", true, 200}, {"GET", "/codebuddy/v1/credentials", "", true, 200}, {"GET", "/codebuddy/v1/credentials/current", "", true, 200},
		{"GET", "/api/console/requests", "", true, 200}, {"GET", "/api/console/overview", "", true, 200}, {"GET", "/api/console/requests/export", "", true, 200}, {"GET", "/api/request-logs", "", true, 200}, {"GET", "/api/request-logs/export", "", true, 200}, {"GET", "/api/console/requests/999", "", true, 404},
		{"GET", "/api/console/requests?period=custom&start=2&end=1", "", true, 422}, {"GET", "/api/console/requests?page=0", "", true, 422}, {"GET", "/api/console/requests?offset=9999", "", true, 422}, {"GET", "/api/console/requests?status=unknown", "", true, 422},
		{"POST", "/codebuddy/v1/chat/completions", "null", true, 400}, {"POST", "/codebuddy/v1/chat/completions", "[]", true, 400}, {"POST", "/codebuddy/v1/chat/completions", "{}{}", true, 400}, {"POST", "/codebuddy/v1/chat/completions", `{"messages":[]}`, true, 400}, {"POST", "/codebuddy/v1/responses", `{"model":"m"}`, true, 400},
		{"POST", "/codebuddy/auth/poll", "{}", true, 400}, {"POST", "/workbuddy/auth/poll", "{}", true, 400}, {"POST", "/codebuddy/auth/reauthorize", `{"filename":"missing.json"}`, true, 404}, {"POST", "/workbuddy/auth/reauthorize", `{"filename":"missing.json"}`, true, 404}, {"POST", "/api/console/credentials/mode", `{"mode":"manual","filename":"missing"}`, true, 409}, {"POST", "/workbuddy/credentials/delete", `{"index":0}`, true, 409},
	} {
		t.Run(tc.method+" "+tc.path+" "+tc.body, func(t *testing.T) {
			w := call(a, tc.method, tc.path, tc.body, tc.auth)
			if w.Code != tc.want {
				t.Fatalf("got %d want %d: %s", w.Code, tc.want, w.Body.String())
			}
		})
	}
	if calls.Load() != 0 {
		t.Fatal("unexpected upstream traffic", calls.Load())
	}
	for _, dir := range []string{".codebuddy_creds", ".workbuddy_creds"} {
		if _, err := os.Stat(filepath.Join(root, dir)); !os.IsNotExist(err) {
			t.Fatal("read-only API created credentials directory")
		}
	}
	cors := call(a, "OPTIONS", "/api/settings", "", false)
	if cors.Code != 204 || cors.Header().Get("Access-Control-Allow-Origin") != "*" {
		t.Fatal(cors.Code, cors.Header())
	}
}

const answer = "data: {\"id\":\"reply\",\"model\":\"mock-model\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"hello\"},\"finish_reason\":null}]}\n\ndata: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":2,\"completion_tokens\":3,\"total_tokens\":5}}\n\ndata: [DONE]\n\n"

func TestHTTPChatFailoverAndPersistentDiagnostics(t *testing.T) {
	var calls atomic.Int32
	a, _ := setup(t, doer(func(r *http.Request) (*http.Response, error) {
		if r.URL.Path != "/v2/chat/completions" {
			return response(404, `{}`), nil
		}
		calls.Add(1)
		var body data
		json.NewDecoder(r.Body).Decode(&body)
		if body["_credential_filename"] != nil || body["stream"] != true {
			t.Error("selector leaked upstream")
		}
		if r.Header.Get("Authorization") == "Bearer exhausted" {
			return response(200, "data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"}}]}\n\ndata: {\"error\":{\"message\":\"积分不足\"}}\n\n"), nil
		}
		return response(200, answer), nil
	}))
	a.Code.Put("a.json", data{"bearer_token": "exhausted", "user_id": "a"})
	a.Code.Put("b.json", data{"bearer_token": "usable", "user_id": "b"})
	w := call(a, "POST", "/codebuddy/v1/chat/completions", `{"model":"mock-model","messages":[{"role":"user","content":"hello"}],"stream":false}`, true)
	if w.Code != 200 || calls.Load() != 2 {
		t.Fatal(w.Code, w.Body.String(), calls.Load())
	}
	result := decode(t, w)
	if result.Object("usage").Number("total_tokens") != 5 {
		t.Fatal(result)
	}
	logs := decode(t, call(a, "GET", "/api/console/requests", "", true))
	items := logs["items"].([]any)
	row := data(items[0].(map[string]any))
	if row.Text("credential") != "b.json" || row.Number("upstream_attempts") != 2 || row.Text("status") != "success" {
		t.Fatal(row)
	}
	detail := decode(t, call(a, "GET", fmt.Sprintf("/api/console/requests/%d", row.Number("id")), "", true))
	if len(detail["attempts"].([]any)) != 2 {
		t.Fatal(detail)
	}
	for _, stream := range []bool{true, false} {
		w = call(a, "POST", "/codebuddy/v1/responses", fmt.Sprintf(`{"model":"mock-model","input":"hello","stream":%v}`, stream), true)
		if w.Code != 200 {
			t.Fatal(w.Body.String())
		}
		if stream && !strings.Contains(w.Body.String(), "event: response.completed") {
			t.Fatal(w.Body.String())
		}
	}
	if calls.Load() != 4 {
		t.Fatal("normal requests rotated or repeated", calls.Load())
	}
	snapshot, _ := a.Router.Snapshot()
	if snapshot["current_index"] != 1 {
		t.Fatal(snapshot)
	}
}
func jwt(email string) string {
	b, _ := json.Marshal(data{"email": email, "preferred_username": email, "sub": email})
	return "header." + base64.RawURLEncoding.EncodeToString(b) + ".signature"
}
func TestHTTPLoginAndReauthorization(t *testing.T) {
	for _, product := range []string{"codebuddy", "workbuddy"} {
		t.Run(product, func(t *testing.T) {
			var starts, polls atomic.Int32
			var tokenMu sync.Mutex
			access := jwt("person@example.com")
			a, root := setup(t, doer(func(r *http.Request) (*http.Response, error) {
				switch r.URL.Path {
				case "/v2/plugin/auth/state":
					n := starts.Add(1)
					return response(200, fmt.Sprintf(`{"code":0,"data":{"state":"s%d","authUrl":"https://login.invalid"}}`, n)), nil
				case "/v2/plugin/auth/token":
					if polls.Add(1) == 1 {
						return response(200, `{"code":11217}`), nil
					}
					tokenMu.Lock()
					defer tokenMu.Unlock()
					return response(200, fmt.Sprintf(`{"code":0,"data":{"accessToken":%q,"refreshToken":"refresh","expiresIn":86400}}`, access)), nil
				case "/v2/plugin/login/account":
					return response(200, `{"code":0,"data":{"uid":"person","phone":"12345"}}`), nil
				}
				return response(404, `{}`), nil
			}))
			start := decode(t, call(a, "GET", "/"+product+"/auth/start", "", true))
			body := `{"auth_state":"` + start.Text("auth_state") + `"}`
			pending := call(a, "POST", "/"+product+"/auth/poll", body, true)
			if pending.Code != 400 || decode(t, pending).Text("error") != "authorization_pending" {
				t.Fatal(pending.Body.String())
			}
			w := call(a, "POST", "/"+product+"/auth/poll", body, true)
			if w.Code != 200 || strings.Contains(w.Body.String(), "refresh") || strings.Contains(w.Body.String(), "access_token") {
				t.Fatal(w.Body.String())
			}
			directory := filepath.Join(root, "."+product+"_creds")
			repo := &credential.JSONRepository{Directory: directory}
			records, _ := repo.List()
			if len(records) != 1 {
				t.Fatal(records)
			}
			name := records[0].Filename
			if product == "codebuddy" {
				a.Router.SetMode("manual", name)
			}
			renew := decode(t, call(a, "POST", "/"+product+"/auth/reauthorize", fmt.Sprintf(`{"filename":%q}`, name), true))
			tokenMu.Lock()
			access = jwt("person@example.com") + "new"
			tokenMu.Unlock()
			w = call(a, "POST", "/"+product+"/auth/reauthorize/poll", `{"auth_state":"`+renew.Text("auth_state")+`"}`, true)
			if w.Code != 200 {
				t.Fatal(w.Body.String())
			}
			records, _ = repo.List()
			if len(records) != 1 || records[0].Filename != name || records[0].Data.Text("bearer_token") != access {
				t.Fatal("reauthorization did not update in place")
			}
			if product == "codebuddy" && a.Router.Mode() != "manual" {
				t.Fatal("manual mode lost")
			}
		})
	}
}
func TestStreamingFlushesBeforeUpstreamCompletion(t *testing.T) {
	release := make(chan struct{})
	var once sync.Once
	defer once.Do(func() { close(release) })
	a, _ := setup(t, doer(func(r *http.Request) (*http.Response, error) {
		reader, writer := io.Pipe()
		go func() {
			defer writer.Close()
			io.WriteString(writer, "data: {\"choices\":[{\"delta\":{\"content\":\"first\"}}]}\n\n")
			select {
			case <-release:
			case <-r.Context().Done():
				return
			}
			io.WriteString(writer, "data: [DONE]\n\n")
		}()
		return &http.Response{StatusCode: 200, Header: http.Header{}, Body: reader}, nil
	}))
	a.Code.Put("a.json", data{"bearer_token": "token"})
	server := httptest.NewServer(a.Handler)
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	request, _ := http.NewRequestWithContext(ctx, "POST", server.URL+"/codebuddy/v1/chat/completions", strings.NewReader(`{"model":"mock","messages":[{"role":"user","content":"hi"}],"stream":true}`))
	request.Header.Set("Authorization", "Bearer test-password")
	result, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer result.Body.Close()
	line, err := bufio.NewReader(result.Body).ReadString('\n')
	if err != nil || !strings.Contains(line, "first") {
		t.Fatal(line, err)
	}
	once.Do(func() { close(release) })
	io.Copy(io.Discard, result.Body)
}
func TestBackgroundStopsOnClose(t *testing.T) {
	a, _ := setup(t, doer(func(*http.Request) (*http.Response, error) { return response(200, `{}`), nil }))
	a.Background()
	a.Background()
	stopped := make(chan struct{})
	go func() { a.Close(); close(stopped) }()
	select {
	case <-stopped:
	case <-time.After(2 * time.Second):
		t.Fatal("background tasks did not stop")
	}
}

func TestHTTPMultipleExhaustionAndAllExhausted(t *testing.T) {
	for _, all := range []bool{false, true} {
		t.Run(fmt.Sprint(all), func(t *testing.T) {
			var calls atomic.Int32
			a, _ := setup(t, doer(func(r *http.Request) (*http.Response, error) {
				calls.Add(1)
				if all || r.Header.Get("Authorization") != "Bearer c.json" {
					return response(402, `{"error":{"code":"insufficient_quota"}}`), nil
				}
				return response(200, answer), nil
			}))
			for _, name := range []string{"a.json", "b.json", "c.json"} {
				a.Code.Put(name, data{"bearer_token": name})
			}
			request := `{"model":"m","messages":[{"role":"user","content":"hello"}]}`
			w := call(a, "POST", "/codebuddy/v1/chat/completions", request, true)
			want := 200
			if all {
				want = 402
			}
			if w.Code != want || calls.Load() != 3 {
				t.Fatal(w.Code, calls.Load(), w.Body.String())
			}
			w = call(a, "POST", "/codebuddy/v1/chat/completions", request, true)
			if all {
				if w.Code != 402 || calls.Load() != 3 {
					t.Fatal("cooldown sent more requests", w.Code, calls.Load())
				}
			} else if w.Code != 200 || calls.Load() != 4 {
				t.Fatal("did not stay on c", w.Code, calls.Load())
			}
		})
	}
}
func TestHTTPExpiredCredentialRenewalConcurrency(t *testing.T) {
	for _, outcome := range []string{"success", "temporary", "revoked"} {
		t.Run(outcome, func(t *testing.T) {
			var refreshes, chats atomic.Int32
			a, _ := setup(t, doer(func(r *http.Request) (*http.Response, error) {
				switch r.URL.Path {
				case "/v2/plugin/auth/token/refresh":
					refreshes.Add(1)
					switch outcome {
					case "temporary":
						return response(503, `{}`), nil
					case "revoked":
						return response(200, `{"code":12153}`), nil
					}
					return response(200, `{"code":0,"data":{"accessToken":"fresh-access","refreshToken":"fresh-refresh","expiresIn":86400}}`), nil
				case "/v2/chat/completions":
					chats.Add(1)
					if r.Header.Get("Authorization") != "Bearer fresh-access" {
						t.Error("expired token sent to model")
					}
					return response(200, answer), nil
				}
				return response(404, `{}`), nil
			}))
			a.Code.Put("old.json", data{"bearer_token": "expired-access", "refresh_token": "old-refresh", "created_at": 1, "expires_in": 600})
			count := 1
			if outcome == "success" {
				count = 8
			}
			var wg sync.WaitGroup
			for n := 0; n < count; n++ {
				wg.Add(1)
				go func() {
					defer wg.Done()
					w := call(a, "POST", "/codebuddy/v1/chat/completions", `{"model":"m","messages":[{"role":"user","content":"hello"}]}`, true)
					want := map[string]int{"success": 200, "temporary": 503, "revoked": 401}[outcome]
					if w.Code != want {
						t.Error(w.Code, w.Body.String())
					}
				}()
			}
			wg.Wait()
			if refreshes.Load() != 1 {
				t.Fatal("duplicate renewal", refreshes.Load())
			}
			stored, _ := a.Code.Get("old.json")
			switch outcome {
			case "success":
				if chats.Load() != 8 || stored.Text("refresh_token") != "fresh-refresh" {
					t.Fatal(chats.Load(), stored.Text("refresh_token"))
				}
			case "temporary":
				if chats.Load() != 0 || stored.Number("session_dead_at") != 0 {
					t.Fatal("temporary failure invalidated session")
				}
			case "revoked":
				if chats.Load() != 0 || stored.Number("session_dead_at") == 0 {
					t.Fatal("revoked session not marked")
				}
			}
		})
	}
}

func TestLegacyCredentialUsageAndModeCompatibility(t *testing.T) {
	var calls atomic.Int32
	a, _ := setup(t, doer(func(r *http.Request) (*http.Response, error) { calls.Add(1); return response(200, answer), nil }))
	for _, name := range []string{"a.json", "b.json"} {
		if err := a.Code.Put(name, data{"bearer_token": name}); err != nil {
			t.Fatal(err)
		}
	}
	request := `{"model":"m","messages":[{"role":"user","content":"hello"}]}`
	for i := 0; i < 2; i++ {
		if w := call(a, "POST", "/codebuddy/v1/chat/completions", request, true); w.Code != 200 {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	for i := 0; i < 2; i++ {
		current := decode(t, call(a, "GET", "/codebuddy/v1/credentials/current", "", true))
		if current.Number("usage_count") != 2 || current.Text("filename") != "a.json" {
			t.Fatal(current)
		}
	}
	explicit := `{"model":"m","messages":[{"role":"user","content":"hello"}],"_credential_filename":"b.json"}`
	if w := call(a, "POST", "/codebuddy/v1/chat/completions", explicit, true); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	current := decode(t, call(a, "GET", "/codebuddy/v1/credentials/current", "", true))
	if current.Number("usage_count") != 2 || current.Text("filename") != "a.json" || calls.Load() != 3 {
		t.Fatal(current, calls.Load())
	}
	manual := decode(t, call(a, "POST", "/codebuddy/v1/credentials/set-mode", `{"mode":"manual","index":1}`, true))
	if manual.Number("manual_selected_index") != 1 {
		t.Fatal(manual)
	}
	auto := decode(t, call(a, "POST", "/codebuddy/v1/credentials/set-mode", `{"mode":"auto"}`, true))
	if auto["manual_selected_index"] != nil {
		t.Fatal(auto)
	}
	if w := call(a, "POST", "/codebuddy/v1/chat/completions", request, true); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	current = decode(t, call(a, "GET", "/codebuddy/v1/credentials/current", "", true))
	if current.Number("usage_count") != 1 || current.Text("filename") != "b.json" {
		t.Fatal(current)
	}
}

func TestProxyPreservesLargeSchemaNumbersAndUsage(t *testing.T) {
	a, _ := setup(t, doer(func(r *http.Request) (*http.Response, error) {
		raw, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(raw), `"enum":[1234567890123456789]`) {
			t.Error("numeric schema changed", string(raw))
		}
		return response(200, answer), nil
	}))
	if err := a.Code.Put("a.json", data{"bearer_token": "token", "user_id": "person"}); err != nil {
		t.Fatal(err)
	}
	request := `{"model":"mock","messages":[{"role":"user","content":"hello"}],"tools":[{"type":"function","function":{"name":"lookup","parameters":{"type":"object","properties":{"id":{"type":"integer","enum":[1234567890123456789]}}}}}],"_credential_index":0}`
	w := call(a, "POST", "/codebuddy/v1/chat/completions", request, true)
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if got := decode(t, w).Object("usage").Number("total_tokens"); got != 5 {
		t.Fatal(got)
	}
	logs := decode(t, call(a, "GET", "/api/console/requests", "", true))
	row := data(logs["items"].([]any)[0].(map[string]any))
	if row.Number("total_tokens") != 5 || row.Number("token_estimated") != 0 {
		t.Fatal(row)
	}
}
