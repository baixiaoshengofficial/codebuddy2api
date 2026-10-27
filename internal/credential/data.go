// Package credential owns account identity, lifecycle and sticky routing.
package credential

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/jsonvalue"
)

type Data map[string]any
type Record struct {
	Filename string
	Data     Data
}

func (d Data) Text(key string) string {
	if d[key] == nil {
		return ""
	}
	return fmt.Sprint(d[key])
}
func (d Data) Number(key string) int64 {
	switch v := d[key].(type) {
	case float64:
		return int64(v)
	case int:
		return int64(v)
	case int64:
		return v
	case json.Number:
		if n, err := v.Int64(); err == nil {
			return n
		}
		n, _ := v.Float64()
		return int64(n)
	case string:
		n, _ := strconv.ParseInt(v, 10, 64)
		return n
	}
	return 0
}
func (d Data) Object(key string) Data {
	switch v := d[key].(type) {
	case Data:
		return v
	case map[string]any:
		return Data(v)
	}
	return Data{}
}
func Clone(d Data) Data {
	b, _ := json.Marshal(d)
	out := Data{}
	jsonvalue.Unmarshal(b, &out)
	return out
}
func Claims(token string) Data {
	parts := strings.Split(token, ".")
	if len(parts) < 3 {
		return Data{}
	}
	b, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	if err != nil {
		return Data{}
	}
	d := Data{}
	if jsonvalue.Unmarshal(b, &d) != nil || d == nil {
		return Data{}
	}
	return d
}
func Expired(d Data, now time.Time) bool {
	if d.Number("session_dead_at") > 0 {
		return true
	}
	created, ttl := d.Number("created_at"), d.Number("expires_in")
	return created > 0 && ttl > 0 && now.Unix() >= created+ttl-300
}
func Usable(d Data, now time.Time) bool {
	return d.Text("bearer_token") != "" && d.Number("session_dead_at") == 0 && (d.Text("refresh_token") != "" || !Expired(d, now))
}
func first(d Data, keys ...string) string {
	for _, k := range keys {
		if s := d.Text(k); s != "" {
			return s
		}
	}
	return ""
}
func Phone(d Data) string {
	for _, source := range []Data{d, d.Object("user_info")} {
		if v := first(source, "phone", "mobile", "mobile_phone", "phone_number", "telephone"); v != "" {
			return v
		}
	}
	return d.Object("user_info").Text("preferred_username")
}
func Identifiers(d Data) map[string]bool {
	out := map[string]bool{}
	for _, source := range []Data{d, d.Object("user_info"), Claims(d.Text("bearer_token"))} {
		for _, key := range []string{"user_id", "uid", "sub", "email", "preferred_username", "phone", "mobile", "mobile_phone", "phone_number", "telephone"} {
			v := strings.ToLower(strings.TrimSpace(source.Text(key)))
			if v != "" && v != "unknown" && v != strings.ToLower(d.Text("domain")) {
				out[v] = true
			}
		}
	}
	return out
}
func SameIdentity(a, b Data) bool {
	if a.Text("domain") != "" && b.Text("domain") != "" && a.Text("domain") != b.Text("domain") {
		return false
	}
	ids := Identifiers(a)
	for v := range Identifiers(b) {
		if ids[v] {
			return true
		}
	}
	return false
}

func Build(product string, token Data, now time.Time) Data {
	access := first(token, "access_token", "bearer_token")
	claims := Claims(access)
	info := Data{}
	fields := []string{"sub", "email", "preferred_username", "name", "given_name", "family_name", "exp", "iat", "scope"}
	if product == "workbuddy" {
		fields = append(fields, "phone", "mobile", "mobile_phone", "phone_number", "telephone")
	}
	for _, k := range fields {
		if claims[k] != nil {
			info[k] = claims[k]
		}
	}
	if claims["sid"] != nil {
		info["session_state"] = claims["sid"]
	}
	user := first(claims, "email", "preferred_username", "sub")
	if product == "workbuddy" {
		user = first(token, "account_uid")
		if user == "" {
			user = first(claims, "preferred_username", "email", "sub")
		}
	} else if user == "" {
		user = token.Text("domain")
	}
	if user == "" {
		user = "unknown"
	}
	out := Data{"bearer_token": access, "user_id": user, "user_info": info, "created_at": now.Unix(), "token_type": "Bearer"}
	for _, k := range []string{"token_type", "expires_in", "refresh_token", "scope", "domain"} {
		if token[k] != nil {
			out[k] = token[k]
		}
	}
	if product == "workbuddy" {
		out["product"] = "WorkBuddy"
		for from, to := range map[string]string{"account_uid": "uid", "phone": "phone", "account_name": "name", "account_email": "email"} {
			if token.Text(from) != "" {
				info[to] = token[from]
			}
		}
		if token.Text("phone") != "" {
			out["phone"] = token["phone"]
		}
	} else if token["session_state"] != nil {
		out["session_state"] = token["session_state"]
	}
	return out
}
func LoginReplacement(old, fresh Data) Data {
	out := Clone(old)
	for _, k := range []string{"session_dead_at", "session_dead_reason", "last_refresh_at", "refresh_token", "expires_in", "session_state", "scope", "domain", "phone", "full_response"} {
		delete(out, k)
	}
	for k, v := range fresh {
		out[k] = v
	}
	return out
}
