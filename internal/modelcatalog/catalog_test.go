package modelcatalog

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestPackageDiscoveryAndSiteIsolation(t *testing.T) {
	var buffer bytes.Buffer
	gz := gzip.NewWriter(&buffer)
	archive := tar.NewWriter(gz)
	for name, content := range map[string]string{"package/product.json": `{"agents":[{"name":"other","models":["wrong"]},{"name":"cli","models":["one","one","two"]}]}`, "package/product.internal.json": `{"agents":[{"tags":["default","model:craft"],"models":["china"]}]}`} {
		archive.WriteHeader(&tar.Header{Name: name, Size: int64(len(content)), Mode: 0600})
		archive.Write([]byte(content))
	}
	archive.Close()
	gz.Close()
	for _, site := range []string{"china", "international"} {
		models, err := ReadPackage(bytes.NewReader(buffer.Bytes()), site)
		if err != nil {
			t.Fatal(err)
		}
		if site == "china" && len(models) != 1 || site == "international" && len(models) != 2 {
			t.Fatal(site, models)
		}
	}
	if _, err := ReadPackage(bytes.NewReader([]byte("invalid")), "china"); err == nil {
		t.Fatal("invalid package accepted")
	}
}

type fakeDoer func(*http.Request) (*http.Response, error)

func (f fakeDoer) Do(r *http.Request) (*http.Response, error) { return f(r) }
func TestFallbackIsImmediateAndRefreshIsSingleFlight(t *testing.T) {
	var calls atomic.Int32
	release := make(chan struct{})
	done := make(chan struct{})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	catalog := Catalog{Context: ctx, Client: fakeDoer(func(r *http.Request) (*http.Response, error) {
		calls.Add(1)
		defer close(done)
		select {
		case <-release:
		case <-ctx.Done():
		}
		return &http.Response{StatusCode: 503, Body: io.NopCloser(strings.NewReader(`{}`)), Header: http.Header{}}, nil
	})}
	for i := 0; i < 20; i++ {
		models := catalog.Models("china")
		if len(models) == 0 {
			t.Fatal("no immediate fallback")
		}
		models[0] = "mutated"
	}
	close(release)
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("refresh did not run")
	}
	if calls.Load() != 1 {
		t.Fatal("duplicate model discovery", calls.Load())
	}
	if catalog.Models("china")[0] == "mutated" {
		t.Fatal("cache escaped to caller")
	}
}
