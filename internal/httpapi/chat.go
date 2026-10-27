package httpapi

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/chat"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/id"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/ledger"
)

type observation struct {
	logs   *ledger.Store
	record int64
}

func (o observation) StartAttempt(number int, name string) int64 {
	if o.record == 0 {
		return 0
	}
	id, err := o.logs.StartAttempt(o.record, number, name)
	logFailure(err)
	return id
}
func (o observation) FinishAttempt(id int64, status int, message string) {
	if id > 0 {
		logFailure(o.logs.FinishAttempt(id, status, message))
	}
}
func (o observation) Switch(before, after, reason string) {
	if o.record > 0 {
		logFailure(o.logs.Switch(before, after, reason, &o.record))
	}
}
func selector(body credential.Data) (string, *int, error) {
	filename := ""
	var index *int
	if v := body["_credential_filename"]; v != nil {
		var ok bool
		filename, ok = v.(string)
		if !ok || filename == "" {
			return "", nil, fault.New(422, "invalid_selector", "_credential_filename must be a non-empty string")
		}
	}
	if v := body["_credential_index"]; v != nil {
		number, ok := v.(json.Number)
		if !ok {
			return "", nil, fault.New(422, "invalid_selector", "_credential_index must be an integer")
		}
		n, err := number.Int64()
		if err != nil || n < 0 || n > 1000000 {
			return "", nil, fault.New(422, "invalid_selector", "_credential_index must be an integer")
		}
		i := int(n)
		index = &i
	}
	delete(body, "_credential_filename")
	delete(body, "_credential_index")
	if filename != "" && index != nil {
		return "", nil, fault.New(422, "invalid_selector", "Specify only one credential selector")
	}
	return filename, index, nil
}
func sse(w http.ResponseWriter, event string, data any) error {
	if event != "" {
		if _, err := fmt.Fprintf(w, "event: %s\n", event); err != nil {
			return err
		}
	}
	var content []byte
	if data == nil {
		content = []byte("[DONE]")
	} else {
		var err error
		content, err = json.Marshal(data)
		if err != nil {
			return err
		}
	}
	if _, err := fmt.Fprintf(w, "data: %s\n\n", content); err != nil {
		return err
	}
	return http.NewResponseController(w).Flush()
}
func streamHeaders(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
}
func (s *Server) chat(w http.ResponseWriter, r *http.Request) {
	body, err := decodeObject(w, r)
	if err != nil {
		writeError(w, err, false)
		return
	}
	filename, index, err := selector(body)
	if err != nil {
		writeError(w, err, false)
		return
	}
	wantStream := false
	if body["stream"] != nil {
		var ok bool
		wantStream, ok = body["stream"].(bool)
		if !ok {
			writeError(w, fault.New(400, "invalid_stream", "stream must be a boolean"), false)
			return
		}
	}
	responses := strings.HasSuffix(r.URL.Path, "/responses")
	input := body
	if responses {
		input, err = chat.ResponsesRequest(body)
		if err != nil {
			writeError(w, fault.New(400, "invalid_request", err.Error()), false)
			return
		}
	}
	if err := chat.Validate(input); err != nil {
		writeError(w, fault.New(400, "invalid_request", err.Error()), false)
		return
	}
	payload, err := chat.Prepare(input)
	if err != nil {
		writeError(w, fault.New(400, "invalid_request", err.Error()), false)
		return
	}
	selection, err := s.d.Ready.Select(r.Context(), filename, index)
	if err != nil {
		writeError(w, err, false)
		return
	}
	requestID := r.Header.Get("X-Request-ID")
	if requestID == "" {
		requestID = id.New()
	}
	canonical, _ := json.Marshal(body)
	hash := sha256.Sum256(canonical)
	host, _, _ := net.SplitHostPort(r.RemoteAddr)
	model := payload.Text("model")
	if model == "" {
		model = "unknown"
	}
	recordID, logErr := s.d.Logs.Start(ledger.Start{RequestID: requestID, ConversationID: r.Header.Get("X-Conversation-ID"), Endpoint: r.URL.Path, Model: model, Client: ledger.DetectClient(r.Header, body), ClientDetail: ledger.Preview(r.Header.Get("User-Agent")), ClientHost: host, Credential: selection.Filename, UserID: selection.Data.Text("user_id"), Preview: ledger.RequestPreview(body), Hash: hex.EncodeToString(hash[:]), Streaming: wantStream})
	logFailure(logErr)
	started := time.Now()
	headers := chat.Headers(s.d.Chat.Endpoint(), selection.Data, r.Header, requestID)
	var emit chat.Emit
	if wantStream && !responses {
		streamHeaders(w)
		emit = func(data chat.Data, done bool) error {
			if done {
				return sse(w, "", nil)
			}
			return sse(w, "", data)
		}
	}
	result, runErr := s.d.Chat.Run(r.Context(), payload, selection, headers, observation{s.d.Logs, recordID}, emit)
	status := 200
	message := ""
	if runErr != nil {
		status = 500
		message = runErr.Error()
		var f *fault.Error
		if errors.As(runErr, &f) {
			status = f.Status
		}
	}
	if recordID > 0 {
		logFailure(s.d.Logs.Finish(recordID, ledger.Finish{Status: status, Attempts: result.Attempts, Latency: time.Since(started).Milliseconds(), Usage: map[string]any(result.Usage), InputEstimate: ledger.Estimate(string(canonical)), OutputEstimate: func() int64 {
			if result.Output == "" {
				return 0
			}
			return ledger.Estimate(result.Output)
		}(), Output: result.Output, Error: message, Credential: result.Credential.Filename, UserID: result.Credential.Data.Text("user_id")}))
	}
	if runErr != nil {
		if wantStream && !responses {
			code := "stream_error"
			var f *fault.Error
			if errors.As(runErr, &f) {
				code = f.Code
			}
			sse(w, "", chat.Data{"error": chat.Data{"message": message, "type": code}})
			sse(w, "", nil)
		} else {
			writeError(w, runErr, false)
		}
		return
	}
	if wantStream && !responses {
		return
	}
	response := result.Response
	if responses {
		response = chat.ResponsesResult(response)
	}
	if !wantStream {
		writeJSON(w, 200, response)
		return
	}
	streamHeaders(w)
	for _, event := range []chat.Data{{"type": "response.created", "response": response}, {"type": "response.output_text.delta", "delta": response["output_text"], "output_index": 0, "content_index": 0}, {"type": "response.completed", "response": response}} {
		if err := sse(w, event.Text("type"), event); err != nil {
			return
		}
	}
	sse(w, "", nil)
}
