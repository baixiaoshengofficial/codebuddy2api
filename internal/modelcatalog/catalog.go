// Package modelcatalog discovers model names without delaying API reads.
package modelcatalog

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sync"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/upstream"
)

var fallback = map[string][]string{
	"china":         {"glm-5.2", "glm-5.1", "glm-5v-turbo", "minimax-m3", "minimax-m2.7", "kimi-k2.7", "kimi-k2.6", "hy3-preview", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v3-2-volc"},
	"international": {"default-model", "gemini-3.1-pro", "gemini-3.0-flash", "gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-3.1-flash-lite", "gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "gpt-5.1-codex", "gpt-5.1-codex-mini", "deepseek-v3-2-volc", "glm-5.0", "kimi-k2.5"},
}

type entry struct {
	models     []string
	checked    time.Time
	refreshing bool
}
type Catalog struct {
	mu          sync.Mutex
	entries     map[string]*entry
	Client      upstream.Doer
	Context     context.Context
	MetadataURL string
}

func (c *Catalog) Models(site string) []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.entries == nil {
		c.entries = map[string]*entry{}
	}
	e := c.entries[site]
	if e == nil {
		models := fallback[site]
		if len(models) == 0 {
			models = fallback["international"]
		}
		e = &entry{models: append([]string{}, models...)}
		c.entries[site] = e
	}
	if !e.refreshing && time.Since(e.checked) > 30*time.Minute {
		e.refreshing = true
		ctx := c.Context
		if ctx == nil {
			ctx = context.Background()
		}
		go func() {
			models, err := c.fetch(ctx, site)
			c.mu.Lock()
			defer c.mu.Unlock()
			e.refreshing = false
			e.checked = time.Now()
			if err == nil {
				e.models = models
			} else {
				e.checked = e.checked.Add(-29 * time.Minute)
			}
		}()
	}
	return append([]string{}, e.models...)
}
func (c *Catalog) fetch(ctx context.Context, site string) ([]string, error) {
	endpoint := c.MetadataURL
	if endpoint == "" {
		endpoint = "https://registry.npmjs.org/@tencent-ai%2Fcodebuddy-code/latest"
	}
	payload, status, err := upstream.JSON(ctx, c.Client, "GET", endpoint, http.Header{}, nil)
	if err != nil || status != 200 {
		return nil, fmt.Errorf("model metadata unavailable")
	}
	dist, _ := payload["dist"].(map[string]any)
	link, _ := dist["tarball"].(string)
	u, err := url.Parse(link)
	if err != nil || u.Scheme != "https" || u.Host != "registry.npmjs.org" {
		return nil, fmt.Errorf("invalid package URL")
	}
	req, err := http.NewRequestWithContext(ctx, "GET", link, nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.Client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("model package unavailable")
	}
	return ReadPackage(io.LimitReader(resp.Body, 64<<20), site)
}
func ReadPackage(reader io.Reader, site string) ([]string, error) {
	gz, err := gzip.NewReader(reader)
	if err != nil {
		return nil, err
	}
	defer gz.Close()
	archive := tar.NewReader(io.LimitReader(gz, 256<<20))
	filename := "package/product.json"
	if site == "china" {
		filename = "package/product.internal.json"
	}
	for {
		header, err := archive.Next()
		if err != nil {
			return nil, err
		}
		if header.Name != filename {
			continue
		}
		if header.Size > 4<<20 {
			return nil, fmt.Errorf("model definition too large")
		}
		var product struct {
			Agents []struct {
				Name   string   `json:"name"`
				Tags   []string `json:"tags"`
				Models []string `json:"models"`
			} `json:"agents"`
		}
		if err := json.NewDecoder(archive).Decode(&product); err != nil {
			return nil, err
		}
		if len(product.Agents) == 0 {
			return nil, fmt.Errorf("no model agent")
		}
		selected := 0
		for i, agent := range product.Agents {
			tags := map[string]bool{}
			for _, tag := range agent.Tags {
				tags[tag] = true
			}
			if agent.Name == "cli" || (tags["default"] && tags["model:craft"]) {
				selected = i
				break
			}
		}
		seen := map[string]bool{}
		models := []string{}
		for _, model := range product.Agents[selected].Models {
			if model != "" && !seen[model] {
				models = append(models, model)
				seen[model] = true
			}
		}
		if len(models) == 0 {
			return nil, fmt.Errorf("no models")
		}
		return models, nil
	}
}
