package config

import (
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func TestPrecedenceAndPersistence(t *testing.T) {
	root := t.TempDir()
	t.Setenv("CODEBUDDY_PASSWORD", "environment-password")
	t.Setenv("CODEBUDDY_HOST", "127.0.0.1")
	os.Mkdir(filepath.Join(root, "config"), 0700)
	os.WriteFile(filepath.Join(root, "config/config.json"), []byte(`{"CODEBUDDY_SITE":"international","CODEBUDDY_HOST":"bad-host"}`), 0600)
	s, err := Load(root)
	if err != nil {
		t.Fatal(err)
	}
	if s.Get("CODEBUDDY_PASSWORD") != "environment-password" || s.Get("CODEBUDDY_HOST") != "127.0.0.1" || s.Site() != "international" {
		t.Fatal("precedence")
	}
	if err := s.Update(map[string]any{"CODEBUDDY_AUTO_CHECKIN": false, "CODEBUDDY_CHECKIN_TIME": "08:30", "CODEBUDDY_PASSWORD": "new-password", "CODEBUDDY_PORT": 1}); err != nil {
		t.Fatal(err)
	}
	again, err := Load(root)
	if err != nil || again.Get("CODEBUDDY_PASSWORD") != "new-password" || again.Enabled() || again.Schedule() != "08:30" {
		t.Fatal(err)
	}
	content, _ := os.ReadFile(filepath.Join(root, "config/config.json"))
	if string(content) == "" {
		t.Fatal("no persisted data")
	}
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 5; j++ {
				s.Get("CODEBUDDY_SITE")
				s.All()
				if err := s.Update(map[string]any{"CODEBUDDY_SITE": "china"}); err != nil {
					t.Error(err)
				}
			}
		}()
	}
	wg.Wait()
}
func TestInvalidSettingsDoNotMutate(t *testing.T) {
	s, err := Load(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, change := range []map[string]any{{"unknown": "value"}, {"CODEBUDDY_PASSWORD": " "}, {"CODEBUDDY_SITE": "unknown"}, {"CODEBUDDY_CHECKIN_TIME": "24:00"}, {"CODEBUDDY_CHECKIN_TIME": "1:00"}, {"CODEBUDDY_AUTO_CHECKIN": "yes"}, {"CODEBUDDY_BARK_URL": "javascript:alert(1)"}, {"CODEBUDDY_BARK_URL": "https://user:pass@example.com"}, {"CODEBUDDY_SITE": true}} {
		for key := range change {
			t.Run(key+":"+stringify(change[key]), func(t *testing.T) {
				if err := s.Update(change); err == nil {
					t.Fatal("accepted invalid setting")
				}
			})
		}
	}
}
func stringify(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return "non-string"
}
func TestPersistenceFailureDoesNotHotReload(t *testing.T) {
	root := t.TempDir()
	s, err := Load(root)
	if err != nil {
		t.Fatal(err)
	}
	before := s.Site()
	os.MkdirAll(filepath.Join(root, "config/config.json"), 0700)
	if err := s.Update(map[string]any{"CODEBUDDY_SITE": "international"}); err == nil {
		t.Fatal("expected save failure")
	}
	if s.Site() != before {
		t.Fatal("memory changed after save failure")
	}
}
