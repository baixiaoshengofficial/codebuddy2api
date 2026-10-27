package httpapi

import (
	"net/http"
	"strings"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/oauth"
)

func (s *Server) auth(r *http.Request) *oauth.Service {
	if strings.HasPrefix(r.URL.Path, "/workbuddy/") {
		return s.d.WorkAuth
	}
	return s.d.CodeAuth
}
func (s *Server) authStart(w http.ResponseWriter, r *http.Request) {
	filename := ""
	if r.Method == "POST" {
		var command struct {
			Filename string `json:"filename"`
		}
		if err := decode(w, r, &command); err != nil {
			writeError(w, err, true)
			return
		}
		if command.Filename == "" {
			writeError(w, fault.New(422, "missing_filename", "请选择需要重新授权的凭证"), true)
			return
		}
		filename = command.Filename
	}
	result, err := s.auth(r).Start(r.Context(), filename)
	if err != nil {
		writeError(w, err, true)
		return
	}
	writeJSON(w, 200, result)
}
func (s *Server) authPoll(w http.ResponseWriter, r *http.Request) {
	var command struct {
		State string `json:"auth_state"`
	}
	if err := decode(w, r, &command); err != nil {
		writeError(w, err, true)
		return
	}
	result, err := s.auth(r).Poll(r.Context(), command.State, strings.Contains(r.URL.Path, "/reauthorize/"))
	if err != nil {
		writeError(w, err, true)
		return
	}
	writeJSON(w, 200, result)
}
func (s *Server) routing(w http.ResponseWriter, r *http.Request) {
	snapshot, err := s.d.Router.Snapshot()
	if err != nil {
		writeError(w, err, false)
		return
	}
	events, err := s.d.Logs.Events()
	if err != nil {
		writeError(w, err, false)
		return
	}
	snapshot["events"] = events
	writeJSON(w, 200, snapshot)
}
func currentName(snapshot credential.Data) string {
	rows, _ := snapshot["credentials"].([]credential.Data)
	for _, row := range rows {
		if row["is_current"] == true {
			return row.Text("filename")
		}
	}
	return ""
}
func (s *Server) mode(w http.ResponseWriter, r *http.Request) {
	var command struct {
		Mode     string `json:"mode"`
		Filename string `json:"filename"`
		Index    *int   `json:"index"`
	}
	if err := decode(w, r, &command); err != nil {
		writeError(w, err, false)
		return
	}
	if command.Index != nil {
		records, err := s.d.Code.List()
		if err != nil {
			writeError(w, err, false)
			return
		}
		if *command.Index < 0 || *command.Index >= len(records) {
			writeError(w, fault.New(400, "invalid_index", "Invalid credential index"), false)
			return
		}
		command.Filename = records[*command.Index].Filename
	}
	before, err := s.d.Router.Snapshot()
	if err != nil {
		writeError(w, err, false)
		return
	}
	if err := s.d.Router.SetMode(command.Mode, command.Filename); err != nil {
		writeError(w, err, false)
		return
	}
	after, err := s.d.Router.Snapshot()
	if err != nil {
		writeError(w, err, false)
		return
	}
	reason := "automatic_mode"
	if command.Mode == "manual" {
		reason = "manual_selection"
	}
	logFailure(s.d.Logs.Switch(currentName(before), currentName(after), reason, nil))
	if strings.HasSuffix(r.URL.Path, "set-mode") {
		var manualIndex any
		if command.Mode == "manual" {
			manualIndex = after["current_index"]
		}
		writeJSON(w, 200, credential.Data{"mode": command.Mode, "manual_selected_index": manualIndex, "message": "Switched to " + command.Mode + " mode"})
		return
	}
	writeJSON(w, 200, after)
}
func (s *Server) deleteCredential(w http.ResponseWriter, r *http.Request) {
	filename := r.PathValue("filename")
	work := strings.HasPrefix(r.URL.Path, "/workbuddy/")
	if r.Method == "POST" {
		var command struct {
			Filename string `json:"filename"`
			Index    *int   `json:"index"`
		}
		if err := decode(w, r, &command); err != nil {
			writeError(w, err, false)
			return
		}
		filename = command.Filename
		if filename == "" && work {
			writeError(w, fault.New(409, "stale_command", "按序号删除已停用，请刷新页面后按具体凭证删除"), false)
			return
		}
		if filename == "" && command.Index != nil {
			records, err := s.d.Code.List()
			if err != nil {
				writeError(w, err, false)
				return
			}
			if *command.Index >= 0 && *command.Index < len(records) {
				filename = records[*command.Index].Filename
			}
		}
	}
	var err error
	if work {
		err = s.d.Work.Delete(filename)
	} else {
		err = s.d.Router.Delete(filename)
	}
	if err != nil {
		writeError(w, err, false)
		return
	}
	writeJSON(w, 200, credential.Data{"success": true, "message": "凭证已删除"})
}
func (s *Server) workCredentials(w http.ResponseWriter, r *http.Request) {
	records, err := s.d.Work.List()
	if err != nil {
		writeError(w, err, false)
		return
	}
	rows := []credential.Data{}
	for i, row := range records {
		rows = append(rows, credential.Info(row, i, time.Now(), "workbuddy"))
	}
	writeJSON(w, 200, credential.Data{"credentials": rows})
}
func (s *Server) legacyCredentials(w http.ResponseWriter, r *http.Request) {
	records, err := s.d.Code.List()
	if err != nil {
		writeError(w, err, false)
		return
	}
	rows := []credential.Data{}
	for i, row := range records {
		info := credential.Info(row, i, time.Now(), "codebuddy")
		info["has_token"] = true
		info["token_preview"] = "[REDACTED]"
		info["time_remaining_str"] = "Unknown"
		if info["time_remaining"] != nil {
			if info["is_expired"] == true {
				info["time_remaining_str"] = "Expired"
			} else {
				expires := row.Data.Number("created_at") + row.Data.Number("expires_in")
				info["time_remaining_str"] = (time.Duration(expires-time.Now().Unix()) * time.Second).String()
			}
		}
		rows = append(rows, info)
	}
	writeJSON(w, 200, credential.Data{"credentials": rows})
}
func (s *Server) current(w http.ResponseWriter, r *http.Request) {
	snapshot, err := s.d.Router.Snapshot()
	if err != nil {
		writeError(w, err, false)
		return
	}
	rows := snapshot["credentials"].([]credential.Data)
	for _, row := range rows {
		if row["is_current"] == true {
			writeJSON(w, 200, credential.Data{"status": snapshot["mode"], "mode": snapshot["mode"], "index": row["index"], "filename": row["filename"], "user_id": row["user_id"], "usage_count": snapshot["usage_count"]})
			return
		}
	}
	writeJSON(w, 200, credential.Data{"status": "no_credentials"})
}
