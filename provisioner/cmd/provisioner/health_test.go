package main

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
	"go.mongodb.org/mongo-driver/v2/mongo/readpref"
)

func mongoPinger(t *testing.T, uri string, selection time.Duration) pinger {
	t.Helper()
	client, err := mongo.Connect(options.Client().ApplyURI(uri).
		SetConnectTimeout(selection).SetServerSelectionTimeout(selection))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = client.Disconnect(ctx)
	})
	return pingFunc(func(ctx context.Context) error { return client.Ping(ctx, readpref.Primary()) })
}

func healthz(t *testing.T, h http.Handler) int {
	t.Helper()
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	return w.Code
}

func TestHealthzPingsMongo(t *testing.T) {
	uri := os.Getenv("MONGO_URI")
	if uri == "" {
		t.Fatal("MONGO_URI is required: health tests run against a real MongoDB and never skip")
	}
	if code := healthz(t, healthHandler(mongoPinger(t, uri, 5*time.Second), 2*time.Second)); code != http.StatusOK {
		t.Fatalf("healthz = %d, want 200", code)
	}
}

func TestHealthzUnavailableWithinDeadline(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := l.Addr().String()
	_ = l.Close() // nothing listens here
	h := healthHandler(mongoPinger(t, "mongodb://"+addr+"/?directConnection=true", 10*time.Second), 300*time.Millisecond)
	start := time.Now()
	if code := healthz(t, h); code != http.StatusServiceUnavailable {
		t.Fatalf("healthz = %d, want 503", code)
	}
	if took := time.Since(start); took > 3*time.Second {
		t.Fatalf("healthz took %v, want it bounded by the 300ms ping deadline", took)
	}
}
