// Package httpapi contains HTTP validation and presentation, not provider policy.
package httpapi

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"path/filepath"
	"strings"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/chat"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/checkin"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/config"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/ledger"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/oauth"
)

type Models interface{ Models(string) []string }
type Dependencies struct {
	Config                      *config.Store
	Code, Work                  credential.Repository
	Router                      *credential.Router
	Ready                       chat.Selector
	Chat                        *chat.Service
	CodeAuth, WorkAuth          *oauth.Service
	Logs                        *ledger.Store
	Checkin                     *checkin.Service
	Models                      Models
	Frontend, Version, Revision string
}
type Server struct {
	d   Dependencies
	mux *http.ServeMux
}

func New(d Dependencies) *Server { s := &Server{d: d, mux: http.NewServeMux()}; s.routes(); return s }
func (s *Server) routes() {
	s.mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"status": "healthy", "service": "codebuddy2api"})
	})
	s.mux.HandleFunc("GET /{$}", s.index)
	s.mux.HandleFunc("GET /admin", s.index)
	s.mux.HandleFunc("GET /assets/{path...}", s.asset)
	s.mux.HandleFunc("GET /docs", s.docs)
	s.mux.HandleFunc("GET /openapi.json", s.openapi)
	for _, path := range []string{"/codebuddy/v1", "/codebuddy/v1/chat/completions", "/codebuddy/v1/responses"} {
		s.mux.HandleFunc("GET "+path, func(w http.ResponseWriter, r *http.Request) {
			writeJSON(w, 200, map[string]any{"object": "endpoint", "endpoint": r.URL.Path, "status": "ok", "service": "codebuddy2api"})
		})
	}
	s.mux.HandleFunc("GET /codebuddy/v1/models", s.models)
	for _, product := range []string{"codebuddy", "workbuddy"} {
		prefix := "/" + product
		s.protected("GET "+prefix+"/auth/start", s.authStart)
		s.protected("POST "+prefix+"/auth/poll", s.authPoll)
		s.protected("POST "+prefix+"/auth/reauthorize", s.authStart)
		s.protected("POST "+prefix+"/auth/reauthorize/poll", s.authPoll)
	}
	s.mux.HandleFunc("GET /codebuddy/auth/callback", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"message": "请返回管理页面继续授权"})
	})
	s.protected("POST /codebuddy/v1/chat/completions", s.chat)
	s.protected("POST /codebuddy/v1/responses", s.chat)
	s.protected("GET /api/settings", s.settings)
	s.protected("POST /api/settings", s.settings)
	s.protected("GET /api/stats", s.stats)
	s.protected("GET /api/checkin", s.checkin)
	s.protected("POST /api/checkin/run", s.checkin)
	s.protected("GET /api/console/status", func(w http.ResponseWriter, r *http.Request) {
		var revision any
		if s.d.Revision != "" {
			revision = s.d.Revision
		}
		writeJSON(w, 200, map[string]any{"status": "healthy", "version": s.d.Version, "revision": revision})
	})
	s.protected("GET /api/console/credentials", s.routing)
	s.protected("POST /api/console/credentials/mode", s.mode)
	s.protected("DELETE /api/console/credentials/{filename}", s.deleteCredential)
	s.protected("GET /workbuddy/credentials", s.workCredentials)
	s.protected("DELETE /workbuddy/credentials/{filename}", s.deleteCredential)
	s.protected("POST /workbuddy/credentials/delete", s.deleteCredential)
	s.protected("GET /codebuddy/v1/credentials", s.legacyCredentials)
	s.protected("POST /codebuddy/v1/credentials/set-mode", s.mode)
	s.protected("GET /codebuddy/v1/credentials/current", s.current)
	s.protected("POST /codebuddy/v1/credentials/delete", s.deleteCredential)
	for _, prefix := range []string{"/api/console/requests", "/api/request-logs"} {
		s.protected("GET "+prefix, s.requests)
		s.protected("GET "+prefix+"/export", s.export)
	}
	s.protected("GET /api/console/requests/{id}", s.detail)
	s.protected("GET /api/console/overview", s.overview)
}
func (s *Server) protected(pattern string, handler http.HandlerFunc) {
	s.mux.HandleFunc(pattern, func(w http.ResponseWriter, r *http.Request) {
		password := s.d.Config.Get("CODEBUDDY_PASSWORD")
		if password == "" {
			writeError(w, fault.New(500, "unconfigured", "CODEBUDDY_PASSWORD is not configured on the server."), false)
			return
		}
		scheme, token, ok := strings.Cut(r.Header.Get("Authorization"), " ")
		if !ok || !strings.EqualFold(scheme, "Bearer") || subtle.ConstantTimeCompare([]byte(token), []byte(password)) != 1 {
			writeError(w, fault.New(403, "invalid_password", "Invalid password"), false)
			return
		}
		handler(w, r)
	})
}
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "*")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method == "OPTIONS" {
		w.WriteHeader(204)
		return
	}
	defer func() {
		if err := recover(); err != nil {
			slog.Error("HTTP handler panic")
			writeError(w, fault.New(500, "internal_error", "内部服务错误"), false)
		}
	}()
	s.mux.ServeHTTP(w, r)
}
func decode(w http.ResponseWriter, r *http.Request, target any) error {
	r.Body = http.MaxBytesReader(w, r.Body, 8<<20)
	decoder := json.NewDecoder(r.Body)
	if err := decoder.Decode(target); err != nil {
		return fault.New(400, "invalid_json", "Request body must be valid JSON")
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return fault.New(400, "invalid_json", "Request body must contain one JSON object")
	}
	return nil
}
func decodeObject(w http.ResponseWriter, r *http.Request) (credential.Data, error) {
	var data credential.Data
	if err := decode(w, r, &data); err != nil {
		return nil, err
	}
	if data == nil {
		return nil, fault.New(400, "invalid_json", "Request body must be a JSON object")
	}
	return data, nil
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		slog.Debug("Response write failed")
	}
}
func writeError(w http.ResponseWriter, err error, oauthResponse bool) {
	var f *fault.Error
	if !errors.As(err, &f) {
		if errors.Is(err, credential.ErrNotFound) {
			f = fault.New(404, "not_found", "凭证不存在，请刷新列表")
		} else {
			slog.Error("Request operation failed", "error", err)
			f = fault.New(500, "internal_error", "操作失败，请稍后重试")
		}
	}
	if oauthResponse {
		writeJSON(w, f.Status, map[string]any{"error": f.Code, "error_description": f.Message})
	} else {
		writeJSON(w, f.Status, map[string]any{"detail": f.Message})
	}
}
func (s *Server) index(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
	w.Header().Set("Pragma", "no-cache")
	w.Header().Set("Expires", "0")
	http.ServeFile(w, r, filepath.Join(s.d.Frontend, "index.html"))
}
func (s *Server) asset(w http.ResponseWriter, r *http.Request) {
	path := r.PathValue("path")
	if !filepath.IsLocal(path) || strings.Contains(path, `\`) {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	http.ServeFile(w, r, filepath.Join(s.d.Frontend, "assets", path))
}
