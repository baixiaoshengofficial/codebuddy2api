package ledger

import (
	"encoding/json"
	"fmt"
	"net/http"
	"regexp"
	"strings"
	"unicode"
)

var secretPatterns = []struct {
	r  *regexp.Regexp
	to string
}{
	{regexp.MustCompile(`(?i)("(?:api[_-]?key|(?:access_|refresh_|bearer_)?token|password|authorization)"\s*:\s*)"[^"\n]*"`), `${1}"[REDACTED]"`},
	{regexp.MustCompile(`(?i)(bearer\s+)[a-z0-9._~+/-]+`), `${1}[REDACTED]`},
	{regexp.MustCompile(`(?i)((?:api[_-]?key|token|password)\s*[:=]\s*)[^\s,;]+`), `${1}[REDACTED]`},
	{regexp.MustCompile(`sk-[a-zA-Z0-9_-]{16,}`), "sk-[REDACTED]"},
}

func text(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	b, _ := json.Marshal(v)
	return string(b)
}
func Preview(value any) string {
	s := strings.Join(strings.Fields(text(value)), " ")
	for _, p := range secretPatterns {
		s = p.r.ReplaceAllString(s, p.to)
	}
	r := []rune(s)
	if len(r) > 500 {
		s = string(r[:500])
	}
	return s
}
func RequestPreview(body map[string]any) string {
	var current map[string]any
	messages, ok := body["messages"].([]any)
	if !ok {
		messages, _ = body["input"].([]any)
	}
	for _, v := range messages {
		m, ok := v.(map[string]any)
		if !ok {
			continue
		}
		if m["role"] == "user" || m["role"] == "tool" {
			current = m
		}
	}
	if current == nil {
		for _, v := range messages {
			m, ok := v.(map[string]any)
			if ok && m["role"] == "assistant" {
				current = m
			}
		}
	}
	if current != nil {
		role := text(current["role"])
		if len(role) > 0 {
			role = strings.ToUpper(role[:1]) + role[1:]
		}
		return Preview(role + ": " + text(current["content"]))
	}
	if body["input"] != nil {
		return Preview(body["input"])
	}
	return "(empty request preview)"
}
func Estimate(v any) int64 {
	r := []rune(text(v))
	cjk, other := 0, 0
	for _, c := range r {
		if unicode.Is(unicode.Han, c) {
			cjk++
		} else {
			other++
		}
	}
	n := int64(cjk + other/4)
	if n < 1 {
		return 1
	}
	return n
}

var signatures = []struct {
	label   string
	markers []string
}{
	{"Hermes", []string{"hermes agent", "hermes-agent", "hermesagent"}}, {"Claude Code", []string{"claude code", "claude-code"}}, {"Codex", []string{"codex"}}, {"CodeBuddy", []string{"codebuddy", "code-buddy"}}, {"Open WebUI", []string{"open-webui", "open webui"}}, {"Cherry Studio", []string{"cherry studio", "cherry-studio"}}, {"Roo Code", []string{"roo code", "roo-code"}}, {"Cline", []string{"cline"}}, {"Cursor", []string{"cursor"}}, {"Continue", []string{"continue.dev", "continue/"}}, {"Aider", []string{"aider"}}, {"LiteLLM", []string{"litellm"}}, {"LangChain", []string{"langchain"}}, {"LlamaIndex", []string{"llamaindex", "llama-index"}}, {"Chatbox", []string{"chatbox"}}, {"curl", []string{"curl"}},
}

func signature(s string) string {
	lower := strings.ToLower(s)
	for _, p := range signatures {
		for _, marker := range p.markers {
			if strings.Contains(lower, marker) {
				return p.label
			}
		}
	}
	return ""
}
func canonical(s string) string {
	if label := signature(s); label != "" {
		return label
	}
	runes := []rune(s)
	if len(runes) > 80 {
		return string(runes[:80])
	}
	return s
}
func DetectClient(h http.Header, body map[string]any) string {
	for _, key := range []string{"x-client-name", "x-agent-name", "x-app-name", "x-ide-name", "x-source"} {
		if v := h.Get(key); v != "" {
			return canonical(v)
		}
	}
	meta, _ := body["metadata"].(map[string]any)
	for _, source := range []map[string]any{meta, body} {
		for _, key := range []string{"client", "client_name", "source", "app", "agent"} {
			if v := text(source[key]); v != "" {
				return canonical(v)
			}
		}
	}
	system := text(body["instructions"])
	messages, _ := body["messages"].([]any)
	if messages == nil {
		messages, _ = body["input"].([]any)
	}
	for _, m := range messages {
		if v, ok := m.(map[string]any); ok && (v["role"] == "system" || v["role"] == "developer") {
			system += " " + text(v["content"])
		}
	}
	if v := signature(system); v != "" {
		return v
	}
	agent := h.Get("User-Agent")
	if c := signature(agent); c != "" {
		return c
	}
	lower := strings.ToLower(agent)
	language := h.Get("x-stainless-lang")
	if strings.Contains(lower, "openai/") || language != "" {
		if language == "" {
			language = "SDK"
			if strings.Contains(lower, "python") {
				language = "Python"
			}
		}
		return fmt.Sprintf("OpenAI %s SDK", language)
	}
	if strings.Contains(lower, "python") || strings.Contains(lower, "httpx") || strings.Contains(lower, "requests") {
		return "Python（未标识）"
	}
	if strings.Contains(lower, "node") || strings.Contains(lower, "javascript") {
		return "Node.js（未标识）"
	}
	if agent == "" {
		return "Unknown"
	}
	return canonical(agent)
}
