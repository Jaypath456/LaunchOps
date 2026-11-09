package connectors

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"launchops/provisioner/internal/jobs"
)

func TestClassifyTable(t *testing.T) {
	inProgress := []byte(`{"error":"in progress"}`)
	exists := []byte(`{"error":"account exists"}`)
	for _, tc := range []struct {
		name      string
		status    int
		body      []byte
		retryable bool
	}{
		{"400 bad request", 400, nil, false},
		{"401 unauthorized", 401, nil, false},
		{"403 forbidden", 403, nil, false},
		{"404 not found", 404, nil, false},
		{"409 account exists", 409, exists, false},
		{"409 empty body", 409, nil, false},
		{"409 in progress", 409, inProgress, true},
		{"422 key body mismatch", 422, []byte(`{"error":"idempotency key body mismatch"}`), false},
		{"429 too many requests", 429, nil, true},
		{"500 internal", 500, nil, true},
		{"502 bad gateway", 502, nil, true},
		{"503 unavailable", 503, []byte(`{"error":"injected error"}`), true},
		{"504 gateway timeout", 504, nil, true},
		{"302 redirect", 302, nil, false},
		{"100 continue", 100, nil, false},
	} {
		if got := Classify(tc.status, tc.body); got != tc.retryable {
			t.Errorf("%s: Classify = %v, want %v", tc.name, got, tc.retryable)
		}
	}
}

func TestIsRetryable(t *testing.T) {
	if IsRetryable(nil) {
		t.Error("nil error is not retryable")
	}
	if !IsRetryable(errors.New("unexpected")) {
		t.Error("unknown error must count as retryable")
	}
	if IsRetryable(&Error{Retryable: false, Err: errors.New("x")}) {
		t.Error("permanent error reported retryable")
	}
}

type seen struct {
	mu     sync.Mutex
	keys   []string
	paths  []string
	bodies []map[string]string
}

func (s *seen) record(r *http.Request) {
	var body map[string]string
	_ = json.NewDecoder(r.Body).Decode(&body)
	s.mu.Lock()
	defer s.mu.Unlock()
	s.keys = append(s.keys, r.Header.Get("Idempotency-Key"))
	s.paths = append(s.paths, r.Method+" "+r.URL.Path)
	s.bodies = append(s.bodies, body)
}

func newClient(t *testing.T, url string, timeout time.Duration) *HTTP {
	t.Helper()
	c, err := NewHTTP(HTTPConfig{BaseURL: url, Timeout: timeout})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(c.CloseIdleConnections)
	return c
}

func request() Request {
	return Request{Connector: "google", IdempotencyKey: "inst1:google",
		Payload: jobs.Payload{Username: "ada", DisplayName: "Ada L", Department: "Eng"}}
}

func TestRequestShapeAndSameKey(t *testing.T) {
	var s seen
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.record(r)
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, `{"external_ref":"ref-1","connector":"google","username":"ada"}`)
	}))
	defer srv.Close()
	c := newClient(t, srv.URL, 2*time.Second)
	for range 2 {
		ref, err := c.CreateAccount(context.Background(), request())
		if err != nil || ref != "ref-1" {
			t.Fatalf("CreateAccount = %q, %v", ref, err)
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for i := range 2 {
		if s.paths[i] != "POST /v1/google/accounts" {
			t.Errorf("path = %q", s.paths[i])
		}
		if s.keys[i] != "inst1:google" {
			t.Errorf("Idempotency-Key = %q", s.keys[i])
		}
		want := map[string]string{"username": "ada", "display_name": "Ada L", "department": "Eng"}
		for k, v := range want {
			if s.bodies[i][k] != v || len(s.bodies[i]) != len(want) {
				t.Errorf("body = %v, want %v", s.bodies[i], want)
			}
		}
	}
}

func assertErr(t *testing.T, err error, retryable bool, status int, contains string) {
	t.Helper()
	var e *Error
	if !errors.As(err, &e) {
		t.Fatalf("err = %v (%T), want *Error", err, err)
	}
	if e.Retryable != retryable || e.Status != status {
		t.Fatalf("err = %v: retryable=%v status=%d, want retryable=%v status=%d", err, e.Retryable, e.Status, retryable, status)
	}
	if !strings.Contains(err.Error(), contains) {
		t.Fatalf("err = %q, want it to contain %q", err, contains)
	}
}

func TestHTTPStatusClassification(t *testing.T) {
	for _, tc := range []struct {
		name      string
		status    int
		body      string
		retryable bool
	}{
		{"409 in progress", 409, `{"error":"in progress"}`, true},
		{"409 account exists", 409, `{"error":"account exists"}`, false},
		{"400", 400, `{"error":"bad"}`, false},
		{"422", 422, `{"error":"idempotency key body mismatch"}`, false},
		{"429", 429, ``, true},
		{"503", 503, `{"error":"injected error"}`, true},
		{"200 without external_ref", 200, `{"ok":true}`, true},
		{"201 unparsable body", 201, `not json`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			}))
			defer srv.Close()
			_, err := newClient(t, srv.URL, 2*time.Second).CreateAccount(context.Background(), request())
			assertErr(t, err, tc.retryable, tc.status, "HTTP")
		})
	}
}

func TestRedirectNotFollowed(t *testing.T) {
	var s seen
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.record(r)
		http.Redirect(w, r, "/elsewhere", http.StatusFound)
	}))
	defer srv.Close()
	_, err := newClient(t, srv.URL, 2*time.Second).CreateAccount(context.Background(), request())
	assertErr(t, err, false, 302, "HTTP 302")
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.paths) != 1 {
		t.Fatalf("server saw %v, want exactly one request", s.paths)
	}
}

func TestTimeoutEvenWithoutCallerDeadline(t *testing.T) {
	release := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}))
	defer srv.Close()
	defer close(release)
	start := time.Now()
	_, err := newClient(t, srv.URL, 200*time.Millisecond).CreateAccount(context.Background(), request())
	assertErr(t, err, true, 0, "timeout")
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("call took %v, want it bounded by the 200ms timeout", elapsed)
	}
}

func TestConnectionClosedWithoutResponse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		conn, _, err := http.NewResponseController(w).Hijack()
		if err != nil {
			panic(err)
		}
		_ = conn.Close()
	}))
	defer srv.Close()
	_, err := newClient(t, srv.URL, 2*time.Second).CreateAccount(context.Background(), request())
	assertErr(t, err, true, 0, "request")
}

// On a reused keep-alive connection, net/http would re-send a request carrying Idempotency-Key by itself.
// The connector must not: one CreateAccount is one request, so the job records the failure.
func TestNoTransportLevelResend(t *testing.T) {
	var mu sync.Mutex
	posts := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		mu.Lock()
		posts++
		n := posts
		mu.Unlock()
		if n == 2 {
			conn, _, err := http.NewResponseController(w).Hijack()
			if err != nil {
				panic(err)
			}
			_ = conn.Close()
			return
		}
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, `{"external_ref":"ref-1"}`)
	}))
	defer srv.Close()
	c := newClient(t, srv.URL, 2*time.Second)
	if _, err := c.CreateAccount(context.Background(), request()); err != nil {
		t.Fatal(err)
	}
	_, err := c.CreateAccount(context.Background(), request()) // reuses the kept-alive connection; server drops it
	assertErr(t, err, true, 0, "request")
	mu.Lock()
	defer mu.Unlock()
	if posts != 2 {
		t.Fatalf("server saw %d POSTs, want 2 (no silent re-send)", posts)
	}
}

func TestConnectionRefused(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := l.Addr().String()
	_ = l.Close()
	_, err = newClient(t, "http://"+addr, 2*time.Second).CreateAccount(context.Background(), request())
	assertErr(t, err, true, 0, "request")
}

func TestInvalidRequestsArePermanent(t *testing.T) {
	c := newClient(t, "http://127.0.0.1:1", time.Second)
	bad := request()
	bad.Connector = "../admin"
	_, err := c.CreateAccount(context.Background(), bad)
	assertErr(t, err, false, 0, "invalid connector")
	bad = request()
	bad.IdempotencyKey = ""
	_, err = c.CreateAccount(context.Background(), bad)
	assertErr(t, err, false, 0, "idempotency key")
}

func TestNewHTTPValidation(t *testing.T) {
	for _, cfg := range []HTTPConfig{
		{BaseURL: "", Timeout: time.Second},
		{BaseURL: "ftp://host", Timeout: time.Second},
		{BaseURL: "http://", Timeout: time.Second},
		{BaseURL: "http://host:8080", Timeout: 0},
	} {
		if _, err := NewHTTP(cfg); err == nil {
			t.Errorf("NewHTTP(%+v) = nil error", cfg)
		}
	}
}
