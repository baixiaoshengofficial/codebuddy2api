package chat

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/upstream"
)

type Selector interface {
	Select(context.Context, string, *int) (credential.Selection, error)
}
type Routing interface {
	Exhausted(string) (string, string, error)
	Mode() string
}
type Observer interface {
	StartAttempt(int, string) int64
	FinishAttempt(int64, int, string)
	Switch(string, string, string)
}
type Service struct {
	Client      upstream.Doer
	Endpoint    func() string
	Credentials Selector
	Routing     Routing
}
type Result struct {
	Response   Data
	Usage      Data
	Output     string
	Attempts   int
	Credential credential.Selection
}
type Emit func(Data, bool) error

var exhausted = errors.New("credit exhausted")
var finished = errors.New("stream finished")

func (s *Service) Run(ctx context.Context, payload Data, selection credential.Selection, headers http.Header, observer Observer, emit Emit) (result Result, err error) {
	result.Credential = selection
	attempted := map[string]bool{}
	for {
		active := result.Credential
		if attempted[active.Filename] {
			return result, fault.New(402, "insufficient_credit", "所有可用凭证积分不足")
		}
		attempted[active.Filename] = true
		result.Attempts++
		attemptID := observer.StartAttempt(result.Attempts, active.Filename)
		var aggregate Aggregator
		started := false
		status := 200
		err = s.once(ctx, payload, headers, emit, &aggregate, &started, &status)
		result.Output = aggregate.Content
		result.Usage = aggregate.Usage
		if err == nil {
			observer.FinishAttempt(attemptID, 200, "")
			result.Response = aggregate.Result()
			return result, nil
		}
		if errors.Is(err, exhausted) {
			observer.FinishAttempt(attemptID, 402, "凭证积分不足")
			allow := active.Auto && s.Routing.Mode() == "auto"
			if allow {
				before, after, stateErr := s.Routing.Exhausted(active.Filename)
				if stateErr != nil {
					return result, stateErr
				}
				if before != after {
					observer.Switch(before, after, "credit_exhausted")
				}
			}
			if !allow || started {
				return result, fault.New(402, "insufficient_credit", "CodeBuddy 凭证积分不足")
			}
			next, selectErr := s.Credentials.Select(ctx, "", nil)
			if selectErr != nil {
				return result, selectErr
			}
			if attempted[next.Filename] {
				return result, fault.New(402, "insufficient_credit", "所有可用凭证积分不足")
			}
			if next.Filename != active.Filename {
				result.Credential = next
				headers = headers.Clone()
				headers.Set("Authorization", "Bearer "+next.Data.Text("bearer_token"))
				headers.Set("X-User-Id", next.Data.Text("user_id"))
			}
			continue
		}
		if f, ok := err.(*fault.Error); ok {
			status = f.Status
		}
		observer.FinishAttempt(attemptID, status, err.Error())
		return result, err
	}
}
func (s *Service) once(ctx context.Context, payload Data, headers http.Header, emit Emit, a *Aggregator, started *bool, status *int) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, "POST", s.Endpoint()+"/v2/chat/completions", bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header = headers.Clone()
	response, err := s.Client.Do(request)
	if err != nil {
		*status = 502
		if ctx.Err() != nil {
			*status = 499
			return fault.New(499, "client_disconnected", "Client disconnected")
		}
		return fault.New(502, "connection_failed", "CodeBuddy upstream connection failed")
	}
	defer response.Body.Close()
	*status = response.StatusCode
	if response.StatusCode != 200 {
		data, _ := io.ReadAll(io.LimitReader(response.Body, 1<<20))
		if Insufficient(string(data)) {
			return exhausted
		}
		code := response.StatusCode
		if code >= 500 {
			code = 502
		}
		return fault.New(code, "upstream_error", fmt.Sprintf("CodeBuddy API error: HTTP %d", response.StatusCode))
	}
	converter := Converter{}
	pending := []Data{}
	pendingSize := 0
	done := false
	err = ReadSSE(response.Body, func(data string) error {
		if data == "[DONE]" {
			done = true
			for _, p := range pending {
				if emit != nil {
					if err := emit(p, false); err != nil {
						return err
					}
				}
			}
			pending = nil
			if emit != nil {
				if err := emit(nil, true); err != nil {
					return err
				}
			}
			return finished
		}
		obj := Data{}
		if json.Unmarshal([]byte(data), &obj) != nil || obj == nil {
			return fault.New(502, "invalid_stream", "CodeBuddy upstream returned invalid stream data")
		}
		if Insufficient(obj) {
			return exhausted
		}
		if obj["error"] != nil {
			return fault.New(502, "upstream_error", "CodeBuddy upstream returned an error")
		}
		obj = converter.Convert(obj)
		meaningful := Meaningful(obj)
		if meaningful {
			*started = true
		}
		a.Add(obj)
		if emit == nil {
			return nil
		}
		if !*started {
			pending = append(pending, obj)
			pendingSize += len(data)
			if pendingSize < 65536 {
				return nil
			}
			*started = true
		}
		for _, p := range pending {
			if err := emit(p, false); err != nil {
				return err
			}
		}
		wasPending := len(pending) > 0 && !meaningful && pendingSize >= 65536
		pending = nil
		if !wasPending {
			return emit(obj, false)
		}
		return nil
	})
	if errors.Is(err, finished) {
		return nil
	}
	if err != nil {
		if errors.Is(err, exhausted) {
			return err
		}
		if ctx.Err() != nil {
			return fault.New(499, "client_disconnected", "Client disconnected")
		}
		if _, ok := err.(*fault.Error); ok {
			return err
		}
		return fault.New(502, "connection_failed", "CodeBuddy upstream stream interrupted")
	}
	if !done {
		return fault.New(502, "incomplete_stream", "CodeBuddy upstream stream ended before [DONE]")
	}
	return nil
}
