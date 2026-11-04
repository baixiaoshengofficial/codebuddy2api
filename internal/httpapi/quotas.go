package httpapi

import (
	"context"
	"net/http"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/quota"
)

type Quotas interface {
	List(context.Context) ([]quota.Result, error)
	Refresh(context.Context, string) (quota.Result, error)
}

func (s *Server) quotas(w http.ResponseWriter, r *http.Request) {
	service, ok := s.d.Quotas[r.PathValue("product")]
	if !ok {
		writeError(w, fault.New(404, "unknown_product", "不支持的凭证产品"), false)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	ctx, cancel := context.WithTimeout(r.Context(), 45*time.Second)
	defer cancel()
	if r.Method == "POST" {
		result, err := service.Refresh(ctx, r.PathValue("filename"))
		if err != nil {
			writeError(w, err, false)
			return
		}
		writeJSON(w, 200, result)
		return
	}
	results, err := service.List(ctx)
	if err != nil {
		writeError(w, err, false)
		return
	}
	writeJSON(w, 200, map[string]any{"accounts": results, "cache_seconds": 300})
}
