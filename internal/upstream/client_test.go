package upstream

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestClientNeverReplaysUncertainPOST(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		io.Copy(io.Discard, r.Body)
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		conn.Close()
	}))
	defer server.Close()
	client := NewClient(time.Second)
	defer client.CloseIdleConnections()
	_, _, err := JSON(context.Background(), client, "POST", server.URL, http.Header{"Content-Type": {"application/json"}}, map[string]any{"prompt": "test"})
	if err == nil || calls.Load() != 1 {
		t.Fatal("billed request replayed", calls.Load(), err)
	}
}
func TestClientDoesNotFollowCredentialRedirects(t *testing.T) {
	var forwarded atomic.Int32
	other := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { forwarded.Add(1) }))
	defer other.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, other.URL, http.StatusTemporaryRedirect)
	}))
	defer server.Close()
	client := NewClient(time.Second)
	defer client.CloseIdleConnections()
	_, status, _ := JSON(context.Background(), client, "POST", server.URL, http.Header{"Authorization": {"Bearer secret"}}, map[string]any{})
	if status != 307 || forwarded.Load() != 0 {
		t.Fatal("credential request redirected", status, forwarded.Load())
	}
}
