package main

import (
	"context"
	"encoding/json"
	"net/http"
	"time"
)

type pinger interface {
	Ping(ctx context.Context) error
}

type pingFunc func(ctx context.Context) error

func (f pingFunc) Ping(ctx context.Context) error { return f(ctx) }

// healthHandler serves GET /healthz: 200 when MongoDB answers a ping within timeout, else 503.
func healthHandler(db pinger, timeout time.Duration) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), timeout)
		defer cancel()
		status, body := http.StatusOK, map[string]string{"status": "ok"}
		if err := db.Ping(ctx); err != nil {
			status, body = http.StatusServiceUnavailable, map[string]string{"status": "unavailable", "error": "mongodb ping failed"}
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(body)
	})
	return mux
}
