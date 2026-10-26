// Package config owns validated, hot-reloadable configuration.
package config

import (
	"bufio"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/jsonfile"
)

var Defaults = map[string]any{
	"CODEBUDDY_HOST": "127.0.0.1", "CODEBUDDY_PORT": 8001, "CODEBUDDY_PASSWORD": nil,
	"CODEBUDDY_SITE": "china", "CODEBUDDY_CREDS_DIR": ".codebuddy_creds", "WORKBUDDY_CREDS_DIR": ".workbuddy_creds",
	"CODEBUDDY_LOG_LEVEL": "INFO", "CODEBUDDY_AUTO_CHECKIN": "true", "CODEBUDDY_CHECKIN_TIME": "11:00", "CODEBUDDY_BARK_URL": "",
}
var Readonly = []string{"CODEBUDDY_HOST", "CODEBUDDY_PORT", "CODEBUDDY_CREDS_DIR", "WORKBUDDY_CREDS_DIR", "CODEBUDDY_LOG_LEVEL"}
var Labels = map[string]string{
	"CODEBUDDY_HOST": "服务主机地址", "CODEBUDDY_PORT": "服务端口", "CODEBUDDY_PASSWORD": "API 服务访问密码",
	"CODEBUDDY_SITE": "CodeBuddy 站点 (international/china)", "CODEBUDDY_CREDS_DIR": "CodeBuddy 凭证文件目录",
	"WORKBUDDY_CREDS_DIR": "WorkBuddy 签到凭证目录", "CODEBUDDY_LOG_LEVEL": "日志级别",
	"CODEBUDDY_AUTO_CHECKIN": "自动领取每日签到奖励", "CODEBUDDY_CHECKIN_TIME": "每日自动签到时间（北京时间 HH:MM）", "CODEBUDDY_BARK_URL": "签到结果 Bark 推送地址",
}
var editable = map[string]bool{"CODEBUDDY_PASSWORD": true, "CODEBUDDY_SITE": true, "CODEBUDDY_AUTO_CHECKIN": true, "CODEBUDDY_CHECKIN_TIME": true, "CODEBUDDY_BARK_URL": true}
var clockPattern = regexp.MustCompile(`^(?:[01]\d|2[0-3]):[0-5]\d$`)

type Store struct {
	mu                sync.RWMutex
	path              string
	values, overrides map[string]any
}

func Load(root string) (*Store, error) {
	loadDotEnv(filepath.Join(root, ".env"))
	s := &Store{path: filepath.Join(root, "config/config.json"), values: map[string]any{}, overrides: map[string]any{}}
	for key, fallback := range Defaults {
		s.values[key] = fallback
		if v, ok := os.LookupEnv(key); ok {
			s.values[key] = v
		}
	}
	raw := map[string]any{}
	if err := jsonfile.Read(s.path, &raw); err != nil && !os.IsNotExist(err) {
		return nil, fmt.Errorf("load settings: %w", err)
	}
	for key, value := range raw {
		if editable[key] {
			s.overrides[key] = value
			s.values[key] = value
		}
	}
	return s, nil
}
func loadDotEnv(path string) {
	f, err := os.Open(path)
	if err != nil {
		return
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		key = strings.TrimSpace(strings.TrimPrefix(key, "export "))
		if !ok || key == "" {
			continue
		}
		if _, exists := os.LookupEnv(key); exists {
			continue
		}
		value = strings.TrimSpace(value)
		if len(value) > 1 && (value[0] == '\'' && value[len(value)-1] == '\'' || value[0] == '"' && value[len(value)-1] == '"') {
			value = value[1 : len(value)-1]
		}
		os.Setenv(key, value)
	}
}
func (s *Store) Get(key string) string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.values[key] == nil {
		return ""
	}
	return fmt.Sprint(s.values[key])
}
func (s *Store) All() map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := map[string]any{}
	for k, v := range s.values {
		out[k] = v
	}
	out["CODEBUDDY_SITE"] = normalizeSite(fmt.Sprint(out["CODEBUDDY_SITE"]))
	return out
}
func normalizeSite(site string) string {
	site = strings.ToLower(strings.TrimSpace(site))
	switch site {
	case "cn", "domestic", "mainland":
		return "china"
	case "intl", "global", "overseas":
		return "international"
	}
	return site
}
func (s *Store) Site() string { return normalizeSite(s.Get("CODEBUDDY_SITE")) }
func (s *Store) Endpoint() string {
	if s.Site() == "international" {
		return "https://www.codebuddy.ai"
	}
	return "https://copilot.tencent.com"
}
func (s *Store) Enabled() bool {
	switch strings.ToLower(s.Get("CODEBUDDY_AUTO_CHECKIN")) {
	case "true", "1", "yes", "y", "on":
		return true
	}
	return false
}
func (s *Store) Schedule() string {
	v := s.Get("CODEBUDDY_CHECKIN_TIME")
	if !clockPattern.MatchString(v) {
		return "11:00"
	}
	return v
}
func (s *Store) Address() string {
	port, err := strconv.Atoi(s.Get("CODEBUDDY_PORT"))
	if err != nil || port < 1 || port > 65535 {
		port = 8001
	}
	return fmt.Sprintf("%s:%d", s.Get("CODEBUDDY_HOST"), port)
}
func (s *Store) Update(changes map[string]any) error {
	validated := map[string]any{}
	for key, value := range changes {
		readonly := false
		for _, k := range Readonly {
			if key == k {
				readonly = true
			}
		}
		if readonly {
			continue
		}
		if !editable[key] {
			return validation("不支持修改设置：%s", key)
		}
		if key == "CODEBUDDY_AUTO_CHECKIN" {
			switch v := value.(type) {
			case bool:
				validated[key] = strconv.FormatBool(v)
			case string:
				if v != "true" && v != "false" {
					return validation("自动签到必须选择开启或关闭")
				}
				validated[key] = v
			default:
				return validation("自动签到必须选择开启或关闭")
			}
			continue
		}
		v, ok := value.(string)
		if !ok {
			return validation("%s 必须为文本", key)
		}
		switch key {
		case "CODEBUDDY_PASSWORD":
			if strings.TrimSpace(v) == "" {
				return validation("访问密码不能为空")
			}
		case "CODEBUDDY_SITE":
			if v != "china" && v != "international" {
				return validation("请选择有效的 CodeBuddy 站点")
			}
		case "CODEBUDDY_CHECKIN_TIME":
			if !clockPattern.MatchString(v) {
				return validation("签到时间必须为 HH:MM 格式")
			}
		case "CODEBUDDY_BARK_URL":
			if v != "" {
				u, err := url.Parse(v)
				if err != nil || u.Hostname() == "" || u.User != nil || (u.Scheme != "https" && u.Scheme != "http") {
					return validation("Bark 地址必须为有效的 HTTP 或 HTTPS 地址")
				}
			}
		}
		validated[key] = v
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	next := map[string]any{}
	for k, v := range s.overrides {
		next[k] = v
	}
	for k, v := range validated {
		next[k] = v
	}
	if err := jsonfile.Write(s.path, next); err != nil {
		return err
	}
	s.overrides = next
	for k, v := range validated {
		s.values[k] = v
	}
	return nil
}

func validation(format string, args ...any) error {
	return fault.New(422, "invalid_settings", fmt.Sprintf(format, args...))
}
