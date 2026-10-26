package ledger

import (
	"database/sql"
	"encoding/csv"
	"net/http"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestHistoricalSQLiteMigrationAndQueries(t *testing.T) {
	path := filepath.Join(t.TempDir(), "request_usage.db")
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`CREATE TABLE request_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT NOT NULL,conversation_id TEXT,created_at INTEGER NOT NULL,completed_at INTEGER,endpoint TEXT NOT NULL,model TEXT NOT NULL,client TEXT NOT NULL,client_host TEXT,credential TEXT,credential_user_id TEXT,request_preview TEXT NOT NULL,request_hash TEXT NOT NULL,is_streaming INTEGER NOT NULL DEFAULT 0,status_code INTEGER,status TEXT NOT NULL DEFAULT 'pending',latency_ms INTEGER,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,error TEXT); INSERT INTO request_logs(request_id,created_at,endpoint,model,client,request_preview,request_hash,status)VALUES('old-request',1,'/codebuddy/v1/chat/completions','old-model','curl','old preview','oldhash','success')`)
	if err != nil {
		t.Fatal(err)
	}
	db.Close()
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	old, err := store.Detail(1)
	if err != nil || old["request_id"] != "old-request" {
		t.Fatal(old, err)
	}
	id, err := store.Start(Start{RequestID: "new-request", Endpoint: "/chat", Model: "model", Client: "Codex", Credential: "a.json", UserID: "user", Preview: "User: current", Hash: "hash", Streaming: true})
	if err != nil {
		t.Fatal(err)
	}
	attempt, err := store.StartAttempt(id, 1, "a.json")
	if err != nil {
		t.Fatal(err)
	}
	store.FinishAttempt(attempt, 200, "")
	store.Switch("a.json", "b.json", "credit_exhausted", &id)
	if err := store.Finish(id, Finish{Status: 200, Attempts: 1, Latency: 12, Usage: map[string]any{"prompt_tokens": 2, "completion_tokens": 3}, Output: "password=secret-value", InputEstimate: 100, OutputEstimate: 100}); err != nil {
		t.Fatal(err)
	}
	row, err := store.Detail(id)
	if err != nil || row["total_tokens"] != int64(5) || row["token_estimated"] != int64(0) || len(row["attempts"].([]Data)) != 1 {
		t.Fatal(row, err)
	}
	if strings.Contains(row["response_preview"].(string), "secret-value") {
		t.Fatal("unredacted secret")
	}
	result, err := store.List(Filter{Client: "Codex", Search: "current"}, 1, 20)
	if err != nil || result["total"] != int64(1) {
		t.Fatal(result, err)
	}
	none, err := store.List(Filter{Search: "%' OR 1=1 --"}, 1, 20)
	if err != nil || none["total"] != int64(0) {
		t.Fatal(none, err)
	}
	overview, err := store.Overview(Filter{})
	if err != nil || overview["requests"] != int64(2) || overview["success_rate"] != float64(100) {
		t.Fatal(overview, err)
	}
	output, err := store.Export(Filter{})
	if err != nil {
		t.Fatal(err)
	}
	records, err := csv.NewReader(strings.NewReader(strings.TrimPrefix(string(output), "\ufeff"))).ReadAll()
	if err != nil || len(records) != 3 {
		t.Fatal(records, err)
	}
	stats, err := store.Stats()
	if err != nil || stats["model_usage"].(Data)["old-model"] != int64(1) {
		t.Fatal(stats, err)
	}
}
func TestPreviewRedaction(t *testing.T) {
	for _, value := range []string{`{"password":"secret-value"}`, `Authorization: Bearer secret-value`, `api_key=secret-value`, `sk-1234567890abcdef123456`} {
		t.Run(value, func(t *testing.T) {
			out := Preview(value)
			if strings.Contains(out, "secret-value") || strings.Contains(out, "1234567890abcdef") {
				t.Fatal(out)
			}
		})
	}
	body := map[string]any{"messages": []any{map[string]any{"role": "system", "content": "secret system"}, map[string]any{"role": "user", "content": "old question"}, map[string]any{"role": "assistant", "content": "old answer"}, map[string]any{"role": "user", "content": "current question"}}}
	if RequestPreview(body) != "User: current question" {
		t.Fatal(RequestPreview(body))
	}
}
func TestTimeRanges(t *testing.T) {
	now := time.Date(2026, 10, 26, 1, 0, 0, 0, time.UTC)
	start, end, err := TimeRange("today", nil, nil, 480, now)
	if err != nil || *start != time.Date(2026, 10, 25, 16, 0, 0, 0, time.UTC).UnixMilli() || *end != now.UnixMilli()+1 {
		t.Fatal(start, end, err)
	}
	for _, period := range []string{"24h", "7d", "30d"} {
		if _, _, err := TimeRange(period, nil, nil, 480, now); err != nil {
			t.Fatal(err)
		}
	}
	a, b := int64(1), int64(2)
	if _, _, err := TimeRange("custom", &b, &a, 480, now); err == nil {
		t.Fatal("bad range accepted")
	}
	if _, _, err := TimeRange("custom", &a, &b, 480, now); err != nil {
		t.Fatal(err)
	}
}

func TestClientDetectionPriority(t *testing.T) {
	for _, tc := range []struct {
		name    string
		headers map[string]string
		body    map[string]any
		want    string
	}{
		{"explicit", map[string]string{"X-Client-Name": "Hermes Agent", "User-Agent": "OpenAI/Python"}, nil, "Hermes"},
		{"metadata", map[string]string{"User-Agent": "OpenAI/Python"}, map[string]any{"metadata": map[string]any{"client": "Codex"}}, "Codex"},
		{"system", map[string]string{}, map[string]any{"messages": []any{map[string]any{"role": "system", "content": "You are Claude Code"}}}, "Claude Code"},
		{"Responses instructions", map[string]string{}, map[string]any{"instructions": "You are Codex"}, "Codex"},
		{"SDK", map[string]string{"User-Agent": "OpenAI/Python 1.2.3"}, nil, "OpenAI Python SDK"},
		{"unknown", map[string]string{}, nil, "Unknown"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h := http.Header{}
			for k, v := range tc.headers {
				h.Set(k, v)
			}
			if got := DetectClient(h, tc.body); got != tc.want {
				t.Fatal(got, tc.want)
			}
		})
	}
}
