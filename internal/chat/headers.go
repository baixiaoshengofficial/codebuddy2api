package chat

import (
	"net/http"
	"net/url"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/id"
)

func Headers(endpoint string, account credential.Data, incoming http.Header, requestID string) http.Header {
	u, _ := url.Parse(endpoint)
	h := http.Header{}
	for k, v := range map[string]string{"Accept": "application/json", "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest", "x-stainless-arch": "x64", "x-stainless-lang": "js", "x-stainless-os": "Windows", "x-stainless-package-version": "5.10.1", "x-stainless-retry-count": "0", "x-stainless-runtime": "node", "x-stainless-runtime-version": "v22.13.1", "X-Agent-Intent": "craft", "X-IDE-Type": "CLI", "X-IDE-Name": "CLI", "X-IDE-Version": "1.0.7", "Authorization": "Bearer " + account.Text("bearer_token"), "X-Domain": u.Host, "User-Agent": "CLI/1.0.7 CodeBuddy/1.0.7", "X-Product": "SaaS", "X-User-Id": account.Text("user_id")} {
		h.Set(k, v)
	}
	if h.Get("X-User-Id") == "" {
		h.Set("X-User-Id", "b5be3a67-237e-4ee6-9b9a-0b9ecd7b454b")
	}
	for _, k := range []string{"X-Conversation-ID", "X-Conversation-Request-ID", "X-Conversation-Message-ID", "X-Request-ID"} {
		value := incoming.Get(k)
		if value == "" && k == "X-Request-ID" {
			value = requestID
		}
		if value == "" {
			value = id.New()
		}
		h.Set(k, value)
	}
	return h
}
