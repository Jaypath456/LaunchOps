package mocksaas

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestLatencySettingsValidateAndPersist(t *testing.T) {
	s := newTestService(t, Config{})
	setFaults(t, s, `{"latency_ms":10000,"error_rate":0.5}`)
	for _, patch := range []string{
		`{"latency_ms":-1}`, `{"latency_ms":10001}`, `{"latency_ms":1.5}`,
		`{"latency_ms":null}`, `{"latency_ms":"10"}`, `{"latency_ms":true}`,
		`{"latency_ms":0,"hang_rate":2}`, `{"latency_ms":0,"unknown":1}`,
	} {
		assertStatus(t, call(s, "POST", "/admin/faults", "", patch), http.StatusBadRequest)
	}
	other, err := New(testContext(t), s.db, Config{Faults: Faults{LatencyMS: 1}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	w := call(other, "POST", "/admin/faults", "", `{"error_rate":0}`)
	assertStatus(t, w, http.StatusOK)
	var faults Faults
	if err := json.Unmarshal(w.Body.Bytes(), &faults); err != nil {
		t.Fatal(err)
	}
	if faults.LatencyMS != 10000 || faults.ErrorRate != 0 || faults.HangRate != 0 {
		t.Fatalf("invalid/partial patch changed unrelated settings: %+v", faults)
	}
	setFaults(t, s, `{"latency_ms":0}`)
	assertStatus(t, call(s, "POST", "/v1/slack/accounts", "no-delay", body("alice")), http.StatusCreated)
	if stats := getStats(t, s); stats.LatencyFired != 0 {
		t.Fatalf("disabled latency counted: %+v", stats)
	}
}

func TestLatencyPrecedesStateChangesAndIsCounted(t *testing.T) {
	s := newTestService(t, Config{Faults: Faults{LatencyMS: 1000}})
	started := time.Now()
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() { done <- call(s, "POST", "/v1/slack/accounts", "delayed", body("alice")) }()
	deadline := time.After(5 * time.Second)
	for getStats(t, s).LatencyFired == 0 {
		select {
		case <-deadline:
			t.Fatal("latency did not start")
		case <-time.After(time.Millisecond):
		}
	}
	if count(t, s, "requests") != 0 || count(t, s, "accounts") != 0 {
		t.Fatal("latency wrote application state before the delay elapsed")
	}
	select {
	case w := <-done:
		assertStatus(t, w, http.StatusCreated)
	case <-deadline:
		t.Fatal("delayed request did not finish")
	}
	if elapsed := time.Since(started); elapsed < time.Second {
		t.Fatalf("delay was not applied: %v", elapsed)
	}
	if stats := getStats(t, s); stats.LatencyFired != 1 || stats.Created != 1 {
		t.Fatalf("stats = %+v", stats)
	}
	// Runtime updates apply on the next request, including idempotent replays.
	setFaults(t, s, `{"latency_ms":1}`)
	assertStatus(t, call(s, "POST", "/v1/slack/accounts", "delayed", body("alice")), http.StatusCreated)
	if stats := getStats(t, s); stats.LatencyFired != 2 || stats.Replayed != 1 {
		t.Fatalf("replay latency stats = %+v", stats)
	}
}

func TestLatencyCancelledWithClientRequest(t *testing.T) {
	s := newTestService(t, Config{Faults: Faults{LatencyMS: 10000}})
	handlerDone := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(handlerDone)
		s.ServeHTTP(w, r)
	}))
	defer server.Close()
	client := &http.Client{Timeout: 200 * time.Millisecond}
	r, err := http.NewRequestWithContext(testContext(t), "POST", server.URL+"/v1/slack/accounts", strings.NewReader(body("alice")))
	if err != nil {
		t.Fatal(err)
	}
	r.Header.Set("Idempotency-Key", "cancelled")
	response, err := client.Do(r)
	if response != nil {
		response.Body.Close()
	}
	if err == nil {
		t.Fatal("latency completed before client timeout")
	}
	select {
	case <-handlerDone:
	case <-time.After(2 * time.Second):
		t.Fatal("latency did not stop on client disconnect")
	}
	if stats := getStats(t, s); stats.LatencyFired != 1 || count(t, s, "requests") != 0 || stats.Created != 0 {
		t.Fatalf("cancelled latency performed work or was not counted: %+v", stats)
	}
}
