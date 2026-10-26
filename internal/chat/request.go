// Package chat translates OpenAI requests and executes each billed attempt once.
package chat

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
)

type Data = credential.Data

func Validate(body Data) error {
	messages, ok := body["messages"].([]any)
	if !ok || len(messages) == 0 {
		return fmt.Errorf("Messages field is required and must be a non-empty array")
	}
	for i, v := range messages {
		m, ok := v.(map[string]any)
		if !ok {
			return fmt.Errorf("Message %d must be an object", i)
		}
		if _, ok = m["role"]; !ok {
			return fmt.Errorf("Message %d must have role and content", i)
		}
		if _, ok = m["content"]; !ok {
			return fmt.Errorf("Message %d must have role and content", i)
		}
	}
	return nil
}

var replacements = []struct {
	pattern *regexp.Regexp
	to      string
}{
	{regexp.MustCompile(`(?i)https://github\.com/anthropics/claude-code/issues`), "https://cnb.cool/codebuddy/codebuddy-code/-/issues"},
	{regexp.MustCompile(`(?i)Anthropic's official CLI for Claude`), "Tencent's official CLI for CodeBuddy"},
	{regexp.MustCompile(`(?i)Claude Code`), "CodeBuddy Code"}, {regexp.MustCompile(`(?i)claude-code`), "codebuddy-code"},
	{regexp.MustCompile(`(?i)Anthropic`), "Tencent"}, {regexp.MustCompile(`(?i)Claude`), "CodeBuddy"},
}

func replaceText(v string) string {
	for _, r := range replacements {
		v = r.pattern.ReplaceAllString(v, r.to)
	}
	return v
}
func replaceValue(value any, descriptions bool, key string) any {
	switch v := value.(type) {
	case string:
		if !descriptions || strings.Contains("|content|text|description|instructions|prompt|system|suffix|prefix|", "|"+key+"|") {
			return replaceText(v)
		}
	case []any:
		for i, item := range v {
			v[i] = replaceValue(item, descriptions, key)
		}
		return v
	case map[string]any:
		for k, item := range v {
			v[k] = replaceValue(item, descriptions, k)
		}
		return v
	}
	return value
}
func Prepare(body Data) (Data, error) {
	out := credential.Clone(body)
	out["stream"] = true
	messages, _ := out["messages"].([]any)
	if len(messages) == 1 && Data(messages[0].(map[string]any)).Text("role") == "user" {
		messages = append([]any{map[string]any{"role": "system", "content": "You are a helpful assistant."}}, messages...)
	}
	for _, value := range messages {
		m := value.(map[string]any)
		for _, key := range []string{"content", "tool_calls"} {
			if v, ok := m[key]; ok {
				m[key] = replaceValue(v, false, "")
			}
		}
	}
	out["messages"] = messages
	if choice := out["tool_choice"]; choice != nil {
		switch v := choice.(type) {
		case string:
			if v != "auto" && v != "none" && v != "required" {
				return nil, fmt.Errorf("tool_choice must be auto, none, required, or a named function")
			}
		case map[string]any:
			fn := Data(v).Object("function").Text("name")
			if Data(v).Text("type") != "function" || fn == "" {
				return nil, fmt.Errorf("only named function tool_choice objects are supported")
			}
			tools, _ := out["tools"].([]any)
			matching := []any{}
			for _, tool := range tools {
				t, ok := tool.(map[string]any)
				if ok && Data(t).Text("type") == "function" && Data(t).Object("function").Text("name") == fn {
					matching = append(matching, tool)
				}
			}
			if len(matching) != 1 {
				return nil, fmt.Errorf("tool_choice must name exactly one function in tools")
			}
			out["tools"] = matching
			out["tool_choice"] = "required"
		default:
			return nil, fmt.Errorf("invalid tool_choice")
		}
	} else {
		delete(out, "tool_choice")
	}
	if tools, ok := out["tools"]; ok {
		out["tools"] = replaceValue(tools, true, "")
	}
	return out, nil
}
func ResponsesRequest(body Data) (Data, error) {
	out := Data{"model": body["model"], "stream": false}
	if out.Text("model") == "" {
		return nil, fmt.Errorf("Model field is required")
	}
	messages := []any{}
	if body.Text("instructions") != "" {
		messages = append(messages, map[string]any{"role": "system", "content": body["instructions"]})
	}
	switch v := body["input"].(type) {
	case string:
		if v != "" {
			messages = append(messages, map[string]any{"role": "user", "content": v})
		}
	case []any:
		for _, item := range v {
			m, ok := item.(map[string]any)
			if !ok {
				continue
			}
			role := Data(m).Text("role")
			if role == "" {
				role = "user"
			}
			content := m["content"]
			if parts, ok := content.([]any); ok {
				text := []string{}
				for _, part := range parts {
					if p, ok := part.(map[string]any); ok {
						v := Data(p).Text("text")
						if v == "" {
							v = Data(p).Text("content")
						}
						if v != "" {
							text = append(text, v)
						}
					} else if part != nil {
						text = append(text, fmt.Sprint(part))
					}
				}
				content = strings.Join(text, "\n")
			}
			if content == nil {
				content = ""
			}
			messages = append(messages, map[string]any{"role": role, "content": content})
		}
	}
	if len(messages) == 0 {
		return nil, fmt.Errorf("Responses request requires a non-empty input field")
	}
	out["messages"] = messages
	for _, key := range []string{"temperature", "top_p", "max_tokens", "tools", "tool_choice"} {
		if body[key] != nil {
			out[key] = body[key]
		}
	}
	if body["max_output_tokens"] != nil {
		out["max_tokens"] = body["max_output_tokens"]
	}
	return out, nil
}
