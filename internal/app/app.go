// Package app is the composition root. Domain packages never import it.
package app

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/chat"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/checkin"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/config"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/httpapi"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/ledger"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/modelcatalog"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/oauth"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/upstream"
)

type Options struct {
	Root, Version, Revision string
	Client                  upstream.Doer
	Endpoint                func() string
}
type App struct {
	context    context.Context
	background sync.Once
	control    upstream.Doer
	Handler    http.Handler
	Config     *config.Store
	Logs       *ledger.Store
	Checkin    *checkin.Service
	Code       credential.Repository
	Router     *credential.Router
	Refresh    *credential.Refresher
	cancel     context.CancelFunc
	tasks      sync.WaitGroup
	client     upstream.Doer
}

func New(parent context.Context, options Options) (*App, error) {
	root := options.Root
	if root == "" {
		root = "."
	}
	root, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	settings, err := config.Load(root)
	if err != nil {
		return nil, err
	}
	resolve := func(path string) string {
		if filepath.IsAbs(path) {
			return path
		}
		return filepath.Join(root, path)
	}
	code := &credential.JSONRepository{Directory: resolve(settings.Get("CODEBUDDY_CREDS_DIR"))}
	work := &credential.JSONRepository{Directory: resolve(settings.Get("WORKBUDDY_CREDS_DIR"))}
	router, err := credential.NewRouter(code, credential.JSONState{Path: filepath.Join(code.Directory, "manager_state.json")}, nil)
	if err != nil {
		return nil, err
	}
	logs, err := ledger.Open(filepath.Join(root, "config/request_usage.db"))
	if err != nil {
		return nil, err
	}
	client := options.Client
	if client == nil {
		client = upstream.NewClient(300 * time.Second)
	}
	controlClient := options.Client
	if controlClient == nil {
		controlClient = upstream.NewClient(30 * time.Second)
	}
	endpoint := options.Endpoint
	if endpoint == nil {
		endpoint = settings.Endpoint
	}
	ctx, cancel := context.WithCancel(parent)
	refresh := &credential.Refresher{Repo: code, Client: controlClient, Endpoint: endpoint, Product: "codebuddy"}
	workRefresh := &credential.Refresher{Repo: work, Client: controlClient, Endpoint: endpoint, Product: "workbuddy"}
	ready := credential.Ready{Router: router, Refresher: refresh}
	chatService := &chat.Service{Client: client, Endpoint: endpoint, Credentials: ready, Routing: router}
	codeAuth := &oauth.Service{Provider: &oauth.Client{Product: oauth.CodeBuddy, HTTP: controlClient, Endpoint: endpoint}, Store: code, Accounts: &credential.Accounts{Repo: code, Product: "codebuddy"}, Product: "codebuddy", OnSaved: router.ClearCooldown}
	workAuth := &oauth.Service{Provider: &oauth.Client{Product: oauth.WorkBuddy, HTTP: controlClient, Endpoint: endpoint}, Store: work, Accounts: &credential.Accounts{Repo: work, Product: "workbuddy"}, Product: "workbuddy"}
	checkins, err := checkin.New(work, workRefresh, &checkin.Provider{Client: controlClient, Endpoint: endpoint}, settings, checkin.JSONState{Path: filepath.Join(root, "config/checkin_state.json")})
	if err != nil {
		cancel()
		logs.Close()
		return nil, err
	}
	checkins.Notifier = checkin.Bark{Client: controlClient, URL: func() string { return settings.Get("CODEBUDDY_BARK_URL") }}
	version := options.Version
	if version == "" {
		version = "1.0.0"
	}
	handler := httpapi.New(httpapi.Dependencies{Config: settings, Code: code, Work: work, Router: router, Ready: ready, Chat: chatService, CodeAuth: codeAuth, WorkAuth: workAuth, Logs: logs, Checkin: checkins, Models: &modelcatalog.Catalog{Context: ctx, Client: controlClient}, Frontend: filepath.Join(root, "frontend/dist"), Version: version, Revision: options.Revision})
	return &App{Handler: handler, Config: settings, Logs: logs, Checkin: checkins, Code: code, Router: router, Refresh: refresh, cancel: cancel, client: client, context: ctx, control: controlClient}, nil
}
func (a *App) Background() {
	a.background.Do(func() {
		ctx := a.context
		a.tasks.Add(2)
		go func() { defer a.tasks.Done(); a.Checkin.Loop(ctx) }()
		go func() {
			defer a.tasks.Done()
			timer := time.NewTimer(time.Minute)
			defer timer.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-timer.C:
					records, err := a.Code.List()
					if err != nil {
						slog.Error("Credential keepalive list failed")
					} else {
						for _, r := range records {
							if _, err := a.Refresh.Ensure(ctx, credential.Selection{Record: r}, false); err != nil && ctx.Err() == nil {
								slog.Warn("Credential keepalive failed", "filename", r.Filename)
							}
						}
					}
					timer.Reset(time.Hour)
				}
			}
		}()
	})
}
func (a *App) Close() error {
	a.cancel()
	a.tasks.Wait()
	if client, ok := a.client.(*http.Client); ok {
		client.CloseIdleConnections()
	}
	if client, ok := a.control.(*http.Client); ok {
		client.CloseIdleConnections()
	}
	return a.Logs.Close()
}
func Version() string {
	if v := os.Getenv("CODEBUDDY_VERSION"); v != "" {
		return v
	}
	return "1.0.0"
}
