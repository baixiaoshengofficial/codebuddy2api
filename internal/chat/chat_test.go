package chat

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
)

func object(t *testing.T, s string) Data {
	t.Helper()
	var d Data
	if err := json.Unmarshal([]byte(s), &d); err != nil {
		t.Fatal(err)
	}
	return d
}
func TestRequestCompatibility(t *testing.T) {
	body := object(t, `{"model":"test","stream":false,"messages":[{"role":"user","content":"Claude Code"}],"tools":[{"type":"function","function":{"name":"Claude_tool","description":"Ask Claude","parameters":{"type":"object","properties":{"Claude_key":{"type":"string","description":"Anthropic input"}}}}}],"tool_choice":{"type":"function","function":{"name":"Claude_tool"}}}`)
	if err := Validate(body); err != nil {
		t.Fatal(err)
	}
	payload, err := Prepare(body)
	if err != nil {
		t.Fatal(err)
	}
	if payload["stream"] != true || payload["tool_choice"] != "required" {
		t.Fatal(payload)
	}
	messages := payload["messages"].([]any)
	if len(messages) != 2 || messages[1].(map[string]any)["content"] != "CodeBuddy Code" {
		t.Fatal(messages)
	}
	tool := Data(payload["tools"].([]any)[0].(map[string]any)).Object("function")
	if tool.Text("name") != "Claude_tool" || tool.Text("description") != "Ask CodeBuddy" || tool.Object("parameters").Object("properties")["Claude_key"] == nil {
		t.Fatal(tool)
	}
	if body["stream"] != false || len(body["messages"].([]any)) != 1 {
		t.Fatal("input mutated")
	}
}
func TestBadRequests(t *testing.T) {
	for _, input := range []string{`{}`, `{"messages":[]}`, `{"messages":[null]}`, `{"messages":[{"role":"user"}]}`, `{"messages":["bad"]}`} {
		t.Run(input, func(t *testing.T) {
			if Validate(object(t, input)) == nil {
				t.Fatal("accepted")
			}
		})
	}
	for _, choice := range []string{`"invalid"`, `1`, `{"type":"function","function":{"name":"missing"}}`, `{"type":"tool"}`} {
		t.Run(choice, func(t *testing.T) {
			input := object(t, `{"messages":[{"role":"user","content":"Hi"}],"tool_choice":`+choice+`}`)
			if _, err := Prepare(input); err == nil {
				t.Fatal("accepted")
			}
		})
	}
}
func TestResponsesConversion(t *testing.T) {
	body := object(t, `{"model":"m","instructions":"sys","input":[{"role":"user","content":[{"type":"input_text","text":"hello"},{"text":"world"}]}],"max_output_tokens":25}`)
	converted, err := ResponsesRequest(body)
	if err != nil {
		t.Fatal(err)
	}
	if converted.Number("max_tokens") != 25 || len(converted["messages"].([]any)) != 2 {
		t.Fatal(converted)
	}
	var aggregate Aggregator
	aggregate.Add(object(t, `{"model":"m","choices":[{"delta":{"content":"hello"}}]}`))
	result := ResponsesResult(aggregate.Result())
	if result.Text("output_text") != "hello" || result.Text("status") != "completed" {
		t.Fatal(result)
	}
}
func TestSSEMultilineAndToolAggregation(t *testing.T) {
	raw := ": ping\r\ndata: {\r\ndata: \"id\":\"id\",\"model\":\"m\",\"choices\":[{\"delta\":{\"tool_calls\":[{\"id\":\"tooluse_a\",\"index\":0,\"type\":\"function\",\"function\":{\"name\":\"one\",\"arguments\":\"{\\\"x\\\":\"}}]}}]}\r\n\r\n"
	converter := Converter{}
	var a Aggregator
	consume := func(event string) error { obj := object(t, event); a.Add(converter.Convert(obj)); return nil }
	if err := ReadSSE(strings.NewReader(raw), consume); err != nil {
		t.Fatal(err)
	}
	for _, event := range []string{`{"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"1}"}}]}}]}`, `{"choices":[{"delta":{"tool_calls":[{"id":"tooluse_b","index":0,"function":{"name":"two","arguments":"{}"}}]}}]}`, `{"choices":[{"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}`} {
		consume(event)
	}
	result := a.Result()
	choice := result["choices"].([]any)[0].(Data)
	tools := choice.Object("message")["tool_calls"].([]any)
	if len(tools) != 2 {
		t.Fatal(result)
	}
	first := tools[0].(Data)
	if first.Text("id") != "call_a" || first.Object("function").Text("arguments") != `{"x":1}` {
		t.Fatal(first)
	}
	if a.Usage.Number("total_tokens") != 5 {
		t.Fatal(a.Usage)
	}
}
func TestCreditClassification(t *testing.T) {
	for _, tc := range []struct {
		value string
		want  bool
	}{{`{"error":{"data":{"code":"insufficient_quota"}}}`, true}, {`{"code":402,"msg":"积分不足"}`, true}, {`{"error":{"message":"rate limit exceeded"}}`, false}, {`{"choices":[{"delta":{"content":"积分不足"}}]}`, false}, {`{"error":{"message":"network timeout"}}`, false}} {
		t.Run(tc.value, func(t *testing.T) {
			if got := Insufficient(tc.value); got != tc.want {
				t.Fatal(got)
			}
		})
	}
}

type mockDoer func(*http.Request) (*http.Response, error)

func (f mockDoer) Do(r *http.Request) (*http.Response, error) { return f(r) }

type selectionQueue struct {
	selections []credential.Selection
	calls      int
}

func (q *selectionQueue) Select(context.Context, string, *int) (credential.Selection, error) {
	q.calls++
	if len(q.selections) == 0 {
		return credential.Selection{}, fault.New(402, "insufficient_credit", "all exhausted")
	}
	s := q.selections[0]
	q.selections = q.selections[1:]
	return s, nil
}

type routing struct {
	mode   string
	marked []string
}

func (r *routing) Mode() string { return r.mode }
func (r *routing) Exhausted(name string) (string, string, error) {
	r.marked = append(r.marked, name)
	return name, "next", nil
}

type observer struct {
	starts, finishes []int
	switches         int
}

func (o *observer) StartAttempt(n int, _ string) int64 {
	o.starts = append(o.starts, n)
	return int64(n)
}
func (o *observer) FinishAttempt(_ int64, status int, _ string) {
	o.finishes = append(o.finishes, status)
}
func (o *observer) Switch(string, string, string) { o.switches++ }
func response(status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(body))}
}
func account(name string, auto bool) credential.Selection {
	return credential.Selection{Record: credential.Record{Filename: name, Data: credential.Data{"bearer_token": name, "user_id": name}}, Auto: auto}
}

const metadata = "data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"}}]}\n\n"
const content = "data: {\"id\":\"ok\",\"choices\":[{\"delta\":{\"content\":\"hello\"}}]}\n\n"
const quota = "data: {\"error\":{\"message\":\"insufficient credits\"}}\n\n"
const done = "data: [DONE]\n\n"

func TestAttemptPolicy(t *testing.T) {
	for _, tc := range []struct {
		name, first           string
		status                int
		mode                  string
		auto, stream          bool
		wantCalls, wantStatus int
		wantMark              bool
	}{
		{"stream failover", metadata + quota, 200, "auto", true, true, 2, 0, true},
		{"aggregate failover", metadata + quota, 200, "auto", true, false, 2, 0, true},
		{"HTTP quota failover", `{"error":{"message":"积分不足"}}`, 400, "auto", true, false, 2, 0, true},
		{"partial stream", content + quota, 200, "auto", true, true, 1, 402, true},
		{"partial aggregate", content + quota, 200, "auto", true, false, 1, 402, true},
		{"manual lock", quota, 200, "manual", false, true, 1, 402, false},
		{"explicit test", quota, 200, "auto", false, false, 1, 402, false},
		{"rate limit", `{"error":{"message":"too many requests"}}`, 429, "auto", true, false, 1, 429, false},
		{"server error", `{}`, 503, "auto", true, false, 1, 502, false},
		{"unfinished stream", content, 200, "auto", true, true, 1, 502, false},
		{"invalid stream", "data: not-json\n\n", 200, "auto", true, false, 1, 502, false},
		{"success", metadata + content + done, 200, "auto", true, true, 1, 0, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			client := mockDoer(func(r *http.Request) (*http.Response, error) {
				calls++
				if calls == 1 {
					return response(tc.status, tc.first), nil
				}
				if r.Header.Get("Authorization") != "Bearer b" {
					t.Error(r.Header)
				}
				return response(200, metadata+content+done), nil
			})
			queue := &selectionQueue{selections: []credential.Selection{account("b", true)}}
			route := &routing{mode: tc.mode}
			obs := &observer{}
			service := Service{Client: client, Endpoint: func() string { return "https://example.invalid" }, Credentials: queue, Routing: route}
			emitted := []Data{}
			doneCount := 0
			var emit Emit
			if tc.stream {
				emit = func(d Data, done bool) error {
					if done {
						doneCount++
					} else {
						emitted = append(emitted, d)
					}
					return nil
				}
			}
			result, err := service.Run(context.Background(), Data{}, account("a", tc.auto), http.Header{"Authorization": {"Bearer a"}}, obs, emit)
			if calls != tc.wantCalls || result.Attempts != tc.wantCalls {
				t.Fatal(calls, result.Attempts)
			}
			if tc.wantStatus == 0 {
				if err != nil {
					t.Fatal(err)
				}
				if result.Output != "hello" {
					t.Fatal(result.Output)
				}
				if tc.stream && doneCount != 1 {
					t.Fatal(doneCount)
				}
				if tc.stream && len(emitted) != 2 {
					t.Fatal("metadata leaked/duplicated", emitted)
				}
			} else {
				var f *fault.Error
				if !errors.As(err, &f) || f.Status != tc.wantStatus {
					t.Fatal(err)
				}
			}
			if (len(route.marked) > 0) != tc.wantMark {
				t.Fatal(route.marked)
			}
			if len(obs.finishes) != tc.wantCalls {
				t.Fatal(obs.finishes)
			}
		})
	}
}
func TestTransportFailureNeverReplays(t *testing.T) {
	calls := 0
	s := Service{Client: mockDoer(func(*http.Request) (*http.Response, error) { calls++; return nil, io.ErrUnexpectedEOF }), Endpoint: func() string { return "https://test.invalid" }, Credentials: &selectionQueue{}, Routing: &routing{mode: "auto"}}
	_, err := s.Run(context.Background(), Data{}, account("a", true), http.Header{}, &observer{}, nil)
	if err == nil || calls != 1 {
		t.Fatal(calls, err)
	}
}
func TestNoCredentialRetriedInOneRequest(t *testing.T) {
	calls := 0
	queue := &selectionQueue{selections: []credential.Selection{account("b", true), account("a", true)}}
	s := Service{Client: mockDoer(func(*http.Request) (*http.Response, error) { calls++; return response(200, quota), nil }), Endpoint: func() string { return "https://test.invalid" }, Credentials: queue, Routing: &routing{mode: "auto"}}
	_, err := s.Run(context.Background(), Data{}, account("a", true), http.Header{}, &observer{}, nil)
	if err == nil || calls != 2 {
		t.Fatal(calls, err)
	}
}
func TestHeadersPreserveConversation(t *testing.T) {
	incoming := http.Header{}
	for _, name := range []string{"X-Conversation-ID", "X-Conversation-Request-ID", "X-Conversation-Message-ID", "X-Request-ID"} {
		incoming.Set(name, "original-"+name)
	}
	headers := Headers("https://test.invalid", credential.Data{"bearer_token": "secret", "user_id": "person"}, incoming, "fallback")
	for name, values := range incoming {
		if headers.Get(name) != values[0] {
			t.Fatal(name)
		}
	}
	if headers.Get("x-stainless-retry-count") != "0" || headers.Get("Authorization") != "Bearer secret" {
		t.Fatal("missing protocol header")
	}
}
func TestReaderCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	s := Service{Client: mockDoer(func(*http.Request) (*http.Response, error) { return nil, ctx.Err() }), Endpoint: func() string { return "https://test.invalid" }, Routing: &routing{mode: "auto"}, Credentials: &selectionQueue{}}
	_, err := s.Run(ctx, Data{}, account("a", true), http.Header{}, &observer{}, nil)
	var f *fault.Error
	if !errors.As(err, &f) || f.Status != 499 {
		t.Fatal(fmt.Sprint(err))
	}
}

func TestToolContinuationWithRepeatedAndDistinctIndices(t *testing.T) {
	for _, secondIndex := range []int{0, 1} {
		t.Run(fmt.Sprint(secondIndex), func(t *testing.T) {
			var aggregate Aggregator
			convert := Converter{}
			for _, raw := range []string{`{"choices":[{"delta":{"tool_calls":[{"id":"tooluse_a","index":0,"function":{"name":"one","arguments":"{}"}}]}}]}`, fmt.Sprintf(`{"choices":[{"delta":{"tool_calls":[{"id":"tooluse_b","index":%d,"function":{"name":"two","arguments":"{\"value\":"}}]}}]}`, secondIndex), fmt.Sprintf(`{"choices":[{"delta":{"tool_calls":[{"index":%d,"function":{"arguments":"2}"}}]}}]}`, secondIndex)} {
				aggregate.Add(convert.Convert(object(t, raw)))
			}
			result := aggregate.Result()
			tools := result["choices"].([]any)[0].(Data).Object("message")["tool_calls"].([]any)
			if tools[0].(Data).Object("function").Text("arguments") != "{}" || tools[1].(Data).Object("function").Text("arguments") != `{"value":2}` {
				t.Fatal(tools)
			}
		})
	}
}
