package httpapi

import (
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/config"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/ledger"
)

func logFailure(err error) {
	if err != nil {
		slog.Error("Unable to persist diagnostic", "error", err)
	}
}
func (s *Server) settings(w http.ResponseWriter, r *http.Request) {
	if r.Method == "GET" {
		writeJSON(w, 200, map[string]any{"settings": s.d.Config.All(), "labels": config.Labels, "readonly_keys": config.Readonly})
		return
	}
	var command struct {
		Settings map[string]any `json:"settings"`
	}
	if err := decode(w, r, &command); err != nil {
		writeError(w, err, false)
		return
	}
	if command.Settings == nil {
		writeError(w, fault.New(422, "invalid_settings", "settings must be an object"), false)
		return
	}
	if err := s.d.Config.Update(command.Settings); err != nil {
		writeError(w, err, false)
		return
	}
	writeJSON(w, 200, map[string]any{"message": "设置已保存并成功热加载！"})
}
func (s *Server) stats(w http.ResponseWriter, r *http.Request) {
	result, err := s.d.Logs.Stats()
	if err != nil {
		writeError(w, err, false)
		return
	}
	writeJSON(w, 200, result)
}
func (s *Server) checkin(w http.ResponseWriter, r *http.Request) {
	var result map[string]any
	var err error
	if r.Method == "POST" {
		result, err = s.d.Checkin.Run(r.Context(), true, false)
	} else {
		result, err = s.d.Checkin.Status()
	}
	if err != nil {
		writeError(w, err, false)
		return
	}
	writeJSON(w, 200, result)
}
func queryInt(r *http.Request, key string, fallback, min, max int) (int, error) {
	v := r.URL.Query().Get(key)
	if v == "" {
		return fallback, nil
	}
	n, err := strconv.Atoi(v)
	if err != nil || n < min || n > max {
		return 0, fault.New(422, "invalid_query", fmt.Sprintf("Invalid %s", key))
	}
	return n, nil
}
func requestFilter(r *http.Request) (ledger.Filter, error) {
	q := r.URL.Query()
	filter := ledger.Filter{Search: q.Get("search"), Model: q.Get("model"), Client: q.Get("client"), Credential: q.Get("credential"), Status: q.Get("status")}
	if utf8.RuneCountInString(filter.Search) > 200 {
		return filter, fault.New(422, "invalid_query", "搜索内容不能超过 200 字")
	}
	switch filter.Status {
	case "", "success", "error", "pending":
	default:
		return filter, fault.New(422, "invalid_query", "无效的请求状态")
	}
	if strings.HasPrefix(r.URL.Path, "/api/request-logs") {
		days, err := queryInt(r, "days", 1, 0, 3650)
		if err != nil {
			return filter, err
		}
		if days > 0 {
			start := time.Now().Add(-time.Duration(days) * 24 * time.Hour).UnixMilli()
			filter.Start = &start
		}
		return filter, nil
	}
	offset, err := queryInt(r, "offset", 480, -720, 840)
	if err != nil {
		return filter, err
	}
	var start, end *int64
	for key, target := range map[string]**int64{"start": &start, "end": &end} {
		if v := q.Get(key); v != "" {
			n, err := strconv.ParseInt(v, 10, 64)
			if err != nil {
				return filter, fault.New(422, "invalid_query", "无效的时间范围")
			}
			*target = &n
		}
	}
	period := q.Get("period")
	if period == "" {
		period = "today"
	}
	filter.Start, filter.End, err = ledger.TimeRange(period, start, end, offset, time.Now())
	if err != nil {
		return filter, fault.New(422, "invalid_query", err.Error())
	}
	return filter, nil
}
func (s *Server) requests(w http.ResponseWriter, r *http.Request) {
	filter, err := requestFilter(r)
	if err != nil {
		writeError(w, err, false)
		return
	}
	page, err := queryInt(r, "page", 1, 1, 1000000)
	if err != nil {
		writeError(w, err, false)
		return
	}
	size, err := queryInt(r, "page_size", 20, 1, 100)
	if err != nil {
		writeError(w, err, false)
		return
	}
	result, err := s.d.Logs.List(filter, page, size)
	if err != nil {
		writeError(w, err, false)
		return
	}
	writeJSON(w, 200, result)
}
func (s *Server) export(w http.ResponseWriter, r *http.Request) {
	filter, err := requestFilter(r)
	if err != nil {
		writeError(w, err, false)
		return
	}
	content, err := s.d.Logs.Export(filter)
	if err != nil {
		writeError(w, err, false)
		return
	}
	w.Header().Set("Content-Type", "text/csv; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="codebuddy-request-logs.csv"`)
	w.Write(content)
}
func (s *Server) detail(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil || id < 1 {
		writeError(w, fault.New(422, "invalid_id", "Invalid record ID"), false)
		return
	}
	result, err := s.d.Logs.Detail(id)
	if err != nil {
		writeError(w, err, false)
		return
	}
	if result == nil {
		writeError(w, fault.New(404, "not_found", "请求记录不存在"), false)
		return
	}
	writeJSON(w, 200, result)
}
func (s *Server) overview(w http.ResponseWriter, r *http.Request) {
	filter, err := requestFilter(r)
	if err != nil {
		writeError(w, err, false)
		return
	}
	result, err := s.d.Logs.Overview(filter)
	if err != nil {
		writeError(w, err, false)
		return
	}
	writeJSON(w, 200, result)
}
func (s *Server) models(w http.ResponseWriter, r *http.Request) {
	models := s.d.Models.Models(s.d.Config.Site())
	data := []any{}
	for _, model := range models {
		data = append(data, map[string]any{"id": model, "object": "model", "created": time.Now().Unix(), "owned_by": "codebuddy", "capabilities": []string{"chat.completions", "responses"}})
	}
	writeJSON(w, 200, map[string]any{"object": "list", "data": data})
}
