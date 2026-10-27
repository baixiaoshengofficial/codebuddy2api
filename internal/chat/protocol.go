package chat

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	requestid "github.com/baixiaoshengofficial/codebuddy2api/internal/id"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/jsonvalue"
)

func Insufficient(value any) bool {
	if text, ok := value.(string); ok {
		var decoded any
		if jsonvalue.Unmarshal([]byte(text), &decoded) == nil {
			value = decoded
		}
	}
	parts := []string{}
	var collect func(any)
	collect = func(item any) {
		switch v := item.(type) {
		case map[string]any:
			for _, key := range []string{"code", "type", "message", "msg", "detail", "error_description"} {
				if v[key] != nil {
					parts = append(parts, fmt.Sprint(v[key]))
				}
			}
			for _, key := range []string{"error", "data"} {
				if v[key] != nil {
					collect(v[key])
				}
			}
		case Data:
			collect(map[string]any(v))
		case []any:
			for _, nested := range v {
				collect(nested)
			}
		case string:
			parts = append(parts, v)
		}
	}
	collect(value)
	s := strings.ToLower(strings.Join(parts, " "))
	for _, marker := range []string{"积分不足", "积分已耗尽", "余额不足", "余额已耗尽", "额度不足", "额度已用尽", "额度用尽", "额度已耗尽", "点数不足", "insufficient_credit", "insufficient_quota", "insufficient balance", "insufficient credits", "insufficient_balance", "not enough credit", "credit balance is too low", "credits exhausted", "quota exhausted"} {
		if strings.Contains(s, marker) {
			return true
		}
	}
	return false
}
func Meaningful(obj Data) bool {
	if obj["error"] != nil {
		return true
	}
	choices, _ := obj["choices"].([]any)
	for _, value := range choices {
		choice, ok := value.(map[string]any)
		if !ok {
			continue
		}
		if Data(choice).Text("finish_reason") != "" {
			return true
		}
		delta := Data(choice).Object("delta")
		for _, key := range []string{"content", "reasoning_content", "tool_calls", "function_call", "refusal"} {
			switch v := delta[key].(type) {
			case string:
				if v != "" {
					return true
				}
			case []any:
				if len(v) > 0 {
					return true
				}
			case map[string]any:
				if len(v) > 0 {
					return true
				}
			}
		}
	}
	return false
}

type Converter struct {
	indices       map[string]int
	sourceIndices map[int]int
	last          int
}

func (c *Converter) Convert(obj Data) Data {
	if c.indices == nil {
		c.indices = map[string]int{}
		c.sourceIndices = map[int]int{}
	}
	choices, _ := obj["choices"].([]any)
	for _, value := range choices {
		m, ok := value.(map[string]any)
		if !ok {
			continue
		}
		if m["finish_reason"] == "" {
			m["finish_reason"] = nil
		}
		delta := Data(m).Object("delta")
		for _, key := range []string{"reasoning_content", "refusal", "extra_fields"} {
			empty, isString := delta[key].(string)
			if delta[key] == nil || (isString && empty == "") {
				delete(delta, key)
			}
		}
		fn := delta.Object("function_call")
		if fn.Text("name") == "" && fn.Text("arguments") == "" {
			delete(delta, "function_call")
		}
		tools, _ := delta["tool_calls"].([]any)
		if len(tools) == 0 {
			delete(delta, "tool_calls")
		}
		for _, v := range tools {
			tool, ok := v.(map[string]any)
			if !ok {
				continue
			}
			id := Data(tool).Text("id")
			if id != "" {
				index, exists := c.indices[id]
				if !exists {
					index = len(c.indices)
					c.indices[id] = index
				}
				if _, provided := tool["index"]; provided {
					c.sourceIndices[int(Data(tool).Number("index"))] = index
				}
				tool["index"] = index
				c.last = index
				if strings.HasPrefix(id, "tooluse_") {
					tool["id"] = "call_" + strings.TrimPrefix(id, "tooluse_")
				}
			} else if len(c.indices) > 0 {
				mapped, known := c.sourceIndices[int(Data(tool).Number("index"))]
				if _, provided := tool["index"]; provided && known {
					tool["index"] = mapped
				} else {
					tool["index"] = c.last
				}
			}
		}
		m["delta"] = delta
	}
	return obj
}

// ReadSSE accepts CRLF, multiline data and a final event without a blank line.
func ReadSSE(reader io.Reader, consume func(string) error) error {
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 4096), 2<<20)
	data := []string{}
	dispatch := func() error {
		if len(data) == 0 {
			return nil
		}
		event := strings.Join(data, "\n")
		data = nil
		return consume(event)
	}
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if err := dispatch(); err != nil {
				return err
			}
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue
		}
		field, value, _ := strings.Cut(line, ":")
		if strings.HasPrefix(value, " ") {
			value = value[1:]
		}
		if field == "data" {
			data = append(data, value)
		}
	}
	if err := scanner.Err(); err != nil {
		return err
	}
	return dispatch()
}

type tool struct{ ID, Type, Name, Args string }
type Aggregator struct {
	ID, Model, Content, Reasoning, Fingerprint, Finish string
	Usage                                              Data
	tools                                              map[int]*tool
	current                                            int
}

func (a *Aggregator) Add(obj Data) {
	if a.ID == "" {
		a.ID = obj.Text("id")
	}
	if a.Model == "" {
		a.Model = obj.Text("model")
	}
	if obj.Text("system_fingerprint") != "" {
		a.Fingerprint = obj.Text("system_fingerprint")
	}
	if usage := obj.Object("usage"); len(usage) > 0 {
		a.Usage = usage
	}
	choices, _ := obj["choices"].([]any)
	if len(choices) == 0 {
		return
	}
	choice, ok := choices[0].(map[string]any)
	if !ok {
		return
	}
	c := Data(choice)
	if c.Text("finish_reason") != "" {
		a.Finish = c.Text("finish_reason")
	}
	delta := c.Object("delta")
	a.Content += delta.Text("content")
	a.Reasoning += delta.Text("reasoning_content")
	calls, _ := delta["tool_calls"].([]any)
	if a.tools == nil {
		a.tools = map[int]*tool{}
	}
	for _, value := range calls {
		m, ok := value.(map[string]any)
		if !ok {
			continue
		}
		tc := Data(m)
		index := a.current
		if tc["index"] != nil {
			index = int(tc.Number("index"))
		}
		t := a.tools[index]
		if t == nil {
			if tc.Text("id") == "" {
				continue
			}
			t = &tool{ID: tc.Text("id"), Type: "function"}
			a.tools[index] = t
		}
		a.current = index
		if tc.Text("type") != "" {
			t.Type = tc.Text("type")
		}
		fn := tc.Object("function")
		if fn.Text("name") != "" {
			t.Name = fn.Text("name")
		}
		t.Args += fn.Text("arguments")
	}
}
func repairArguments(s string) string {
	s = strings.TrimSpace(s)
	if json.Valid([]byte(s)) {
		return s
	}
	decoder := json.NewDecoder(strings.NewReader(s))
	var first any
	if decoder.Decode(&first) == nil {
		b, _ := json.Marshal(first)
		return string(b)
	}
	for _, suffix := range []string{"}", "]"} {
		if json.Valid([]byte(s + suffix)) {
			return s + suffix
		}
	}
	return "{}"
}
func (a *Aggregator) Result() Data {
	id := a.ID
	if id == "" {
		id = requestid.New()
	}
	model := a.Model
	if model == "" {
		model = "unknown"
	}
	message := Data{"role": "assistant", "content": a.Content}
	if a.Reasoning != "" {
		message["reasoning_content"] = a.Reasoning
	}
	finish := a.Finish
	if finish == "" {
		finish = "stop"
	}
	if len(a.tools) > 0 {
		indices := []int{}
		for i := range a.tools {
			indices = append(indices, i)
		}
		sort.Ints(indices)
		calls := []any{}
		for _, i := range indices {
			t := a.tools[i]
			calls = append(calls, Data{"id": t.ID, "type": t.Type, "function": Data{"name": t.Name, "arguments": repairArguments(t.Args)}})
		}
		message["tool_calls"] = calls
		finish = "tool_calls"
	}
	out := Data{"id": id, "object": "chat.completion", "created": time.Now().Unix(), "model": model, "choices": []any{Data{"index": 0, "message": message, "finish_reason": finish, "logprobs": nil}}}
	if a.Usage != nil {
		out["usage"] = a.Usage
	}
	if a.Fingerprint != "" {
		out["system_fingerprint"] = a.Fingerprint
	}
	return out
}
func ResponsesResult(completion Data) Data {
	choices, _ := completion["choices"].([]any)
	message := Data{}
	if len(choices) > 0 {
		switch c := choices[0].(type) {
		case Data:
			message = c.Object("message")
		case map[string]any:
			message = Data(c).Object("message")
		}
	}
	text := message.Text("content")
	return Data{"id": "resp_" + requestid.New(), "object": "response", "created_at": completion["created"], "status": "completed", "model": completion["model"], "output": []any{Data{"id": "msg_" + requestid.New(), "type": "message", "status": "completed", "role": "assistant", "content": []any{Data{"type": "output_text", "text": text, "annotations": []any{}}}}}, "output_text": text, "usage": completion["usage"]}
}
