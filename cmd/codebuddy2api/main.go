package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/app"
)

var revision string

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		client := http.Client{Timeout: 5 * time.Second}
		port := os.Getenv("CODEBUDDY_PORT")
		if port == "" {
			port = "8001"
		}
		response, err := client.Get("http://127.0.0.1:" + port + "/health")
		if err != nil {
			os.Exit(1)
		}
		response.Body.Close()
		if response.StatusCode != 200 {
			os.Exit(1)
		}
		return
	}
	if err := run(); err != nil {
		slog.Error("Service stopped", "error", err)
		os.Exit(1)
	}
}
func run() error {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	rev := revision
	if v := os.Getenv("CODEBUDDY_REVISION"); v != "" {
		rev = v
	}
	application, err := app.New(ctx, app.Options{Version: app.Version(), Revision: rev})
	if err != nil {
		return err
	}
	defer application.Close()
	level := slog.LevelInfo
	switch application.Config.Get("CODEBUDDY_LOG_LEVEL") {
	case "DEBUG":
		level = slog.LevelDebug
	case "WARNING", "WARN":
		level = slog.LevelWarn
	case "ERROR", "CRITICAL":
		level = slog.LevelError
	}
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: level})))
	application.Background()
	server := &http.Server{Addr: application.Config.Address(), Handler: application.Handler, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 90 * time.Second, MaxHeaderBytes: 1 << 20}
	failed := make(chan error, 1)
	go func() {
		slog.Info("CodeBuddy2API listening", "address", server.Addr, "revision", rev)
		failed <- server.ListenAndServe()
	}()
	select {
	case err := <-failed:
		if err != http.ErrServerClosed {
			return fmt.Errorf("HTTP server: %w", err)
		}
	case <-ctx.Done():
		shutdown, stop := context.WithTimeout(context.Background(), 15*time.Second)
		defer stop()
		if err := server.Shutdown(shutdown); err != nil {
			server.Close()
			return err
		}
	}
	return nil
}
