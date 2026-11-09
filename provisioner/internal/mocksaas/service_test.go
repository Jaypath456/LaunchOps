package mocksaas

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

func testContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	t.Cleanup(cancel)
	return ctx
}

func newTestService(t *testing.T, cfg Config) *Service {
	t.Helper()
	uri := os.Getenv("MONGO_URI")
	if uri == "" {
		t.Skip("MONGO_URI is required for real MongoDB tests")
	}
	client, err := mongo.Connect(options.Client().ApplyURI(uri).
		SetConnectTimeout(5 * time.Second).SetServerSelectionTimeout(5 * time.Second))
	if err != nil {
		t.Fatal(err)
	}
	db := client.Database("mocksaas_test_" + bson.NewObjectID().Hex())
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := db.Drop(ctx); err != nil {
			t.Error(err)
		}
		if err := client.Disconnect(ctx); err != nil {
			t.Error(err)
		}
	})
	s, err := New(testContext(t), db, cfg, slog.New(slog.NewJSONHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func body(username string) string {
	return fmt.Sprintf(`{"username":%q,"display_name":"Test User","department":"IT"}`, username)
}

func call(handler http.Handler, method, path, key, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	if key != "" {
		r.Header.Set("Idempotency-Key", key)
	}
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	return w
}

func assertStatus(t *testing.T, response *httptest.ResponseRecorder, want int) {
	t.Helper()
	if response.Code != want {
		t.Fatalf("status = %d, want %d: %s", response.Code, want, response.Body.String())
	}
}

func getStats(t *testing.T, s http.Handler) Stats {
	t.Helper()
	w := call(s, http.MethodGet, "/admin/stats", "", "")
	assertStatus(t, w, http.StatusOK)
	var stats Stats
	if err := json.Unmarshal(w.Body.Bytes(), &stats); err != nil {
		t.Fatal(err)
	}
	return stats
}

func setFaults(t *testing.T, s http.Handler, patch string) {
	t.Helper()
	assertStatus(t, call(s, http.MethodPost, "/admin/faults", "", patch), http.StatusOK)
}

func count(t *testing.T, s *Service, collection string) int64 {
	t.Helper()
	n, err := s.db.Collection(collection).CountDocuments(testContext(t), bson.D{})
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func TestConcurrentSameKey(t *testing.T) {
	s := newTestService(t, Config{})
	// Independent handlers share only MongoDB; an in-process mutex cannot enforce this invariant.
	other, err := New(testContext(t), s.db, Config{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	const n = 50
	start := make(chan struct{})
	responses := make(chan *httptest.ResponseRecorder, n)
	var workers sync.WaitGroup
	for i := range n {
		workers.Add(1)
		go func() {
			defer workers.Done()
			<-start
			handler := s
			if i%2 == 1 {
				handler = other
			}
			responses <- call(handler, http.MethodPost, "/v1/workspace/accounts", "shared", body("shared"))
		}()
	}
	close(start)
	workers.Wait()
	close(responses)
	var original []byte
	var successes, conflicts int64
	for response := range responses {
		switch response.Code {
		case http.StatusCreated:
			successes++
			if original == nil {
				original = bytes.Clone(response.Body.Bytes())
			}
			if !bytes.Equal(original, response.Body.Bytes()) {
				t.Fatal("replay changed response")
			}
		case http.StatusConflict:
			conflicts++
			if !strings.Contains(response.Body.String(), "in progress") {
				t.Fatal(response.Body.String())
			}
		default:
			t.Fatalf("unexpected response: %d %s", response.Code, response.Body.String())
		}
	}
	if successes == 0 {
		t.Fatal("no request completed")
	}
	if got := count(t, s, "accounts"); got != 1 {
		t.Fatalf("accounts = %d, want 1", got)
	}
	// Removing the unique request-key guard must fail this assertion, even though
	// the independent account-username guard still prevents duplicate accounts.
	if got := count(t, s, "requests"); got != 1 {
		t.Fatalf("claims = %d, want 1", got)
	}
	stats := getStats(t, s)
	if stats.Created != 1 || stats.Replayed != successes-1 || stats.InProgressConflicts != conflicts {
		t.Fatalf("stats = %+v; successes=%d conflicts=%d", stats, successes, conflicts)
	}
}

func TestSameKeyDifferentBody(t *testing.T) {
	s := newTestService(t, Config{})
	assertStatus(t, call(s, "POST", "/v1/workspace/accounts", "key", body("alice")), http.StatusCreated)
	for _, request := range []struct{ path, body string }{
		{"/v1/workspace/accounts", body("bob")},
		{"/v1/workspace/accounts", `{"username":"alice","display_name":"Changed","department":"IT"}`},
		{"/v1/workspace/accounts", `{"username":"alice","display_name":"Test User","department":"HR"}`},
		{"/v1/slack/accounts", body("alice")},
	} {
		assertStatus(t, call(s, "POST", request.path, "key", request.body), http.StatusUnprocessableEntity)
	}
	if count(t, s, "accounts") != 1 {
		t.Fatal("body mismatch created an account")
	}
}

func TestReplayIdenticalAndPersistent(t *testing.T) {
	s := newTestService(t, Config{})
	original := call(s, "POST", "/v1/github/accounts", "key", body("alice"))
	assertStatus(t, original, http.StatusCreated)
	other, err := New(testContext(t), s.db, Config{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	// Whitespace and object key order do not change the account request's meaning.
	replay := call(other, "POST", "/v1/github/accounts", "key", `{
		"department":"IT", "display_name":"Test User", "username":"alice" }`)
	if replay.Code != original.Code || !bytes.Equal(replay.Body.Bytes(), original.Body.Bytes()) {
		t.Fatalf("replay = %d %s, original = %d %s", replay.Code, replay.Body.String(), original.Code, original.Body.String())
	}
	if getStats(t, other).Replayed != 1 {
		t.Fatal("replay counter was not persisted")
	}
}

func TestProcessingReturnsConflict(t *testing.T) {
	s := newTestService(t, Config{})
	assertStatus(t, call(s, "POST", "/v1/slack/accounts", "original", body("alice")), http.StatusCreated)
	var stored requestRecord
	if err := s.db.Collection("requests").FindOne(testContext(t), bson.M{"key": "original"}).Decode(&stored); err != nil {
		t.Fatal(err)
	}
	_, err := s.db.Collection("requests").InsertOne(testContext(t), requestRecord{
		Key: "pending", Fingerprint: stored.Fingerprint, State: "processing",
		AccountID: bson.NewObjectID().Hex(), StartedAt: time.Now().UTC(),
	})
	if err != nil {
		t.Fatal(err)
	}
	w := call(s, "POST", "/v1/slack/accounts", "pending", body("alice"))
	assertStatus(t, w, http.StatusConflict)
	if !strings.Contains(w.Body.String(), "in progress") {
		t.Fatal(w.Body.String())
	}
	assertStatus(t, call(s, "POST", "/v1/slack/accounts", "pending", body("bob")), http.StatusUnprocessableEntity)
	if getStats(t, s).InProgressConflicts != 1 || count(t, s, "accounts") != 1 {
		t.Fatal("processing claim was not respected")
	}
}

func TestAccountUniquenessAndConflictReplay(t *testing.T) {
	s := newTestService(t, Config{})
	assertStatus(t, call(s, "POST", "/v1/slack/accounts", "first", body("alice")), http.StatusCreated)
	conflict := call(s, "POST", "/v1/slack/accounts", "second", body("alice"))
	assertStatus(t, conflict, http.StatusConflict)
	replay := call(s, "POST", "/v1/slack/accounts", "second", body("alice"))
	if replay.Code != conflict.Code || !bytes.Equal(replay.Body.Bytes(), conflict.Body.Bytes()) {
		t.Fatal("conflict replay changed")
	}
	assertStatus(t, call(s, "POST", "/v1/github/accounts", "third", body("alice")), http.StatusCreated)
	if count(t, s, "accounts") != 2 {
		t.Fatal("connector/username uniqueness violated")
	}
}

func TestConcurrentDifferentKeysSameAccount(t *testing.T) {
	s := newTestService(t, Config{})
	const n = 50
	start := make(chan struct{})
	results := make(chan int, n)
	for i := range n {
		go func() {
			<-start
			results <- call(s, "POST", "/v1/slack/accounts", fmt.Sprint(i), body("alice")).Code
		}()
	}
	close(start)
	successes := 0
	for range n {
		switch code := <-results; code {
		case http.StatusCreated:
			successes++
		case http.StatusConflict:
		default:
			t.Fatalf("unexpected status: %d", code)
		}
	}
	// Removing the unique connector/username index fails this guard test.
	if successes != 1 || count(t, s, "accounts") != 1 {
		t.Fatalf("successful creations = %d", successes)
	}
}

func TestDropAfterCommit(t *testing.T) {
	s := newTestService(t, Config{Faults: Faults{DropAfterCommitRate: 1}})
	server := httptest.NewServer(s)
	defer server.Close()
	transport := &http.Transport{DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 5 * time.Second}
	post := func() (*http.Response, error) {
		r, err := http.NewRequestWithContext(testContext(t), "POST", server.URL+"/v1/workspace/accounts", strings.NewReader(body("alice")))
		if err != nil {
			t.Fatal(err)
		}
		r.Header.Set("Idempotency-Key", "dropped")
		return client.Do(r)
	}
	response, err := post()
	if response != nil {
		response.Body.Close()
	}
	if err == nil {
		t.Fatal("drop-after-commit returned a response instead of a connection error")
	}
	if count(t, s, "accounts") != 1 {
		t.Fatal("dropped request did not commit")
	}
	var stored requestRecord
	if err := s.db.Collection("requests").FindOne(testContext(t), bson.M{"key": "dropped"}).Decode(&stored); err != nil {
		t.Fatal(err)
	}
	if stored.State != "done" || stored.Status != http.StatusCreated {
		t.Fatalf("not committed: %+v", stored)
	}
	response, err = post()
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	got, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != stored.Status || !bytes.Equal(got, stored.Body) {
		t.Fatalf("retry did not replay stored result: %d %s", response.StatusCode, got)
	}
	var original Account
	if err := json.Unmarshal(got, &original); err != nil {
		t.Fatal(err)
	}
	var account Account
	if err := s.db.Collection("accounts").FindOne(testContext(t), bson.D{}).Decode(&account); err != nil {
		t.Fatal(err)
	}
	stats := getStats(t, s)
	if original.ExternalRef != account.ExternalRef || stats.Created != 1 || stats.Replayed != 1 || stats.DropAfterCommitFired != 1 {
		t.Fatalf("retry changed the account or counters: %+v", stats)
	}
}

func TestFaultRatesZero(t *testing.T) {
	s := newTestService(t, Config{})
	assertStatus(t, call(s, "POST", "/v1/workspace/accounts", "normal", body("alice")), http.StatusCreated)
	stats := getStats(t, s)
	if stats.Created != 1 || stats.ErrorFired != 0 || stats.HangFired != 0 || stats.DropAfterCommitFired != 0 {
		t.Fatalf("stats = %+v", stats)
	}
}

func TestErrorRateOneAndRuntimeReset(t *testing.T) {
	s := newTestService(t, Config{Faults: Faults{ErrorRate: 1}})
	assertStatus(t, call(s, "POST", "/v1/workspace/accounts", "error", body("alice")), http.StatusServiceUnavailable)
	if count(t, s, "accounts") != 0 || count(t, s, "requests") != 0 {
		t.Fatal("error fault performed account work")
	}
	if getStats(t, s).ErrorFired != 1 {
		t.Fatal("missing error counter")
	}
	setFaults(t, s, `{"error_rate":0}`)
	assertStatus(t, call(s, "POST", "/v1/workspace/accounts", "error", body("alice")), http.StatusCreated)
	setFaults(t, s, `{"error_rate":1}`)
	assertStatus(t, call(s, "POST", "/v1/slack/accounts", "error-again", body("alice")), http.StatusServiceUnavailable)
	if getStats(t, s).ErrorFired != 2 {
		t.Fatal("runtime fault setting not applied")
	}
}

func TestHangRateOneAndRuntimeReset(t *testing.T) {
	s := newTestService(t, Config{Faults: Faults{HangRate: 1}, HangDuration: time.Minute})
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
	r.Header.Set("Idempotency-Key", "hung")
	response, err := client.Do(r)
	if response != nil {
		response.Body.Close()
	}
	if err == nil {
		t.Fatal("hang completed before client timeout")
	}
	select {
	case <-handlerDone:
	case <-time.After(2 * time.Second):
		t.Fatal("hang handler did not notice the client disconnect")
	}
	stats := getStats(t, s)
	if stats.HangFired != 1 || count(t, s, "accounts") != 0 || count(t, s, "requests") != 0 {
		t.Fatalf("hang performed work or was not counted: %+v", stats)
	}
	setFaults(t, s, `{"hang_rate":0}`)
	assertStatus(t, call(s, "POST", "/v1/slack/accounts", "hung", body("alice")), http.StatusCreated)
}

func TestFaultSettingsPersistAndValidate(t *testing.T) {
	s := newTestService(t, Config{})
	setFaults(t, s, `{"error_rate":1,"hang_rate":0.5}`)
	for _, patch := range []string{
		`{"error_rate":-1}`, `{"error_rate":1.1}`, `{"hang_rate":null}`,
		`{"unknown":0}`, `{"drop_after_commit_rate":"1"}`, `{}`, `null`, `[]`,
		`{"error_rate":0} {}`, `{"error_rate":0,"hang_rate":2}`,
	} {
		assertStatus(t, call(s, "POST", "/admin/faults", "", patch), http.StatusBadRequest)
	}
	other, err := New(testContext(t), s.db, Config{Faults: Faults{DropAfterCommitRate: 1}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	w := call(other, "POST", "/admin/faults", "", `{"hang_rate":0}`)
	assertStatus(t, w, http.StatusOK)
	var faults Faults
	if err := json.Unmarshal(w.Body.Bytes(), &faults); err != nil {
		t.Fatal(err)
	}
	if faults.ErrorRate != 1 || faults.HangRate != 0 || faults.DropAfterCommitRate != 0 {
		t.Fatalf("settings did not persist: %+v", faults)
	}
}

func TestValidationAndAdminEndpoints(t *testing.T) {
	s := newTestService(t, Config{})
	for _, request := range []struct{ key, body string }{
		{"", body("alice")}, {"key", "{"}, {"key", "null"}, {"key", "{}"},
		{"key", `{"username":42}`}, {"key", body("alice") + " {}"},
		{"key", `{"username":"alice","unknown":true}`},
		{"key", `{"username":"` + strings.Repeat("a", 65<<10) + `"}`},
	} {
		assertStatus(t, call(s, "POST", "/v1/workspace/accounts", request.key, request.body), http.StatusBadRequest)
	}
	if count(t, s, "requests") != 0 {
		t.Fatal("invalid request claimed a key")
	}
	assertStatus(t, call(s, "GET", "/healthz", "", ""), http.StatusOK)
	empty := call(s, "GET", "/admin/accounts", "", "")
	assertStatus(t, empty, http.StatusOK)
	if empty.Body.String() != "[]" {
		t.Fatalf("empty accounts = %s", empty.Body.String())
	}
	assertStatus(t, call(s, "POST", "/v1/slack/accounts", "valid", body("alice")), http.StatusCreated)
	w := call(s, "GET", "/admin/accounts", "", "")
	assertStatus(t, w, http.StatusOK)
	var accounts []Account
	if err := json.Unmarshal(w.Body.Bytes(), &accounts); err != nil {
		t.Fatal(err)
	}
	if len(accounts) != 1 || accounts[0].ExternalRef == "" || accounts[0].Username != "alice" {
		t.Fatalf("accounts = %+v", accounts)
	}
}

func TestGracefulShutdown(t *testing.T) {
	s := newTestService(t, Config{Faults: Faults{HangRate: 1}, HangDuration: 150 * time.Millisecond})
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	done := make(chan error, 1)
	go func() { done <- s.Serve(ctx, listener) }()
	client := &http.Client{Timeout: 5 * time.Second}
	responseDone := make(chan error, 1)
	go func() {
		r, err := http.NewRequestWithContext(context.Background(), "POST", "http://"+listener.Addr().String()+"/v1/slack/accounts", strings.NewReader(body("alice")))
		if err != nil {
			responseDone <- err
			return
		}
		r.Header.Set("Idempotency-Key", "drain")
		response, err := client.Do(r)
		if err == nil {
			_, err = io.Copy(io.Discard, response.Body)
			response.Body.Close()
			if response.StatusCode != http.StatusServiceUnavailable {
				err = fmt.Errorf("status %d", response.StatusCode)
			}
		}
		responseDone <- err
	}()
	// The hang counter confirms an active request before shutdown begins.
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(time.Millisecond)
	defer ticker.Stop()
	for getStats(t, s).HangFired == 0 {
		select {
		case <-deadline.C:
			t.Fatal("request did not start")
		case <-ticker.C:
		}
	}
	cancel()
	select {
	case err := <-responseDone:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("active request did not drain")
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("server did not shut down")
	}
	response, err := client.Get("http://" + listener.Addr().String() + "/healthz")
	if response != nil {
		response.Body.Close()
	}
	if err == nil {
		t.Fatal("server still accepts requests after shutdown")
	}
}

func TestShutdownBoundsInjectedHang(t *testing.T) {
	s := newTestService(t, Config{Faults: Faults{HangRate: 1}, HangDuration: time.Minute, ShutdownTimeout: 30 * time.Millisecond})
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	done := make(chan error, 1)
	go func() { done <- s.Serve(ctx, listener) }()
	responseDone := make(chan error, 1)
	go func() {
		client := &http.Client{Timeout: 5 * time.Second}
		response, err := client.Post("http://"+listener.Addr().String()+"/v1/slack/accounts", "application/json", strings.NewReader(body("alice")))
		if response != nil {
			response.Body.Close()
		}
		responseDone <- err
	}()
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	for getStats(t, s).HangFired == 0 {
		select {
		case <-deadline.C:
			t.Fatal("request did not start")
		case <-time.After(time.Millisecond):
		}
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("shutdown did not honor deadline")
	}
	select {
	case err := <-responseDone:
		if err == nil {
			t.Fatal("hung connection was not closed")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("hung handler leaked")
	}
}
