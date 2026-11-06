package main

import (
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthcheck(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
	}{
		{"healthy", http.StatusOK},
		{"unavailable", http.StatusServiceUnavailable},
		{"redirect", http.StatusFound},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet || r.URL.Path != "/healthz" {
					t.Errorf("request = %s %s", r.Method, r.URL.Path)
				}
				w.Header().Set("Location", "/healthz")
				w.WriteHeader(tc.status)
			}))
			defer server.Close()
			_, port, err := net.SplitHostPort(server.Listener.Addr().String())
			if err != nil {
				t.Fatal(err)
			}
			// A wildcard listen address must be reached through loopback, even with a proxy set.
			t.Setenv("HTTP_PROXY", "http://127.0.0.1:1")
			err = healthcheck(":" + port)
			if (err == nil) != (tc.status == http.StatusOK) {
				t.Fatalf("healthcheck error = %v, status = %d", err, tc.status)
			}
		})
	}
}

func TestHealthcheckUnreachable(t *testing.T) {
	server := httptest.NewServer(http.NotFoundHandler())
	address := server.Listener.Addr().String()
	server.Close()
	for _, address := range []string{address, "invalid-address"} {
		if err := healthcheck(address); err == nil {
			t.Errorf("healthcheck(%q) succeeded", address)
		}
	}
}
