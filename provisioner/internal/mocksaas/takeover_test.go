package mocksaas

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

// crashOnce returns a CrashAt hook that aborts the first request that reaches stage.
func crashOnce(stage string) func(string) bool {
	var fired atomic.Bool
	return func(at string) bool { return at == stage && fired.CompareAndSwap(false, true) }
}

func fetchRow(t *testing.T, s *Service, key string) requestRecord {
	t.Helper()
	var row requestRecord
	if err := s.db.Collection("requests").FindOne(testContext(t), bson.M{"key": key}).Decode(&row); err != nil {
		t.Fatal(err)
	}
	return row
}

// age moves a claim's lease start into the past.
func age(t *testing.T, s *Service, key string, by time.Duration) {
	t.Helper()
	_, err := s.db.Collection("requests").UpdateOne(testContext(t), bson.M{"key": key},
		bson.M{"$set": bson.M{"started_at": time.Now().UTC().Add(-by)}})
	if err != nil {
		t.Fatal(err)
	}
}

// seedStuck inserts the row a crashed request leaves behind.
func seedStuck(t *testing.T, s *Service, key, connector, username string, startedAgo time.Duration) string {
	t.Helper()
	id := bson.NewObjectID().Hex()
	_, err := s.db.Collection("requests").InsertOne(testContext(t), requestRecord{
		Key: key, Fingerprint: fingerprintOf(connector, accountInput{username, "Test User", "IT"}),
		State: "processing", AccountID: id, StartedAt: time.Now().UTC().Add(-startedAgo),
	})
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func post(t *testing.T, url, path, key, payload string) (int, []byte, error) {
	t.Helper()
	r, err := http.NewRequestWithContext(testContext(t), "POST", url+path, strings.NewReader(payload))
	if err != nil {
		t.Fatal(err)
	}
	r.Header.Set("Idempotency-Key", key)
	client := &http.Client{Transport: &http.Transport{DisableKeepAlives: true}, Timeout: 5 * time.Second}
	response, err := client.Do(r)
	if err != nil {
		return 0, nil, err
	}
	defer response.Body.Close()
	got, err := io.ReadAll(response.Body)
	return response.StatusCode, got, err
}

func assertInProgress(t *testing.T, w *httptest.ResponseRecorder) {
	t.Helper()
	assertStatus(t, w, http.StatusConflict)
	if !strings.Contains(w.Body.String(), `"in progress"`) {
		t.Fatalf("body = %s, want in progress", w.Body.String())
	}
}

func TestCrashAfterClaimBeforeAccountInsert(t *testing.T) {
	s := newTestService(t, Config{})
	s.cfg.Hooks.CrashAt = crashOnce("after_claim")
	server := httptest.NewServer(s)
	defer server.Close()
	if _, _, err := post(t, server.URL, "/v1/slack/accounts", "k", body("alice")); err == nil {
		t.Fatal("crashed request returned a response")
	}
	row := fetchRow(t, s, "k")
	if row.State != "processing" || row.AccountID == "" || count(t, s, "accounts") != 0 {
		t.Fatalf("row = %+v, accounts = %d", row, count(t, s, "accounts"))
	}
	assertInProgress(t, call(s, "POST", "/v1/slack/accounts", "k", body("alice")))
	age(t, s, "k", time.Hour)
	recovered := call(s, "POST", "/v1/slack/accounts", "k", body("alice"))
	assertStatus(t, recovered, http.StatusCreated)
	var account Account
	if err := json.Unmarshal(recovered.Body.Bytes(), &account); err != nil {
		t.Fatal(err)
	}
	if account.ExternalRef != row.AccountID || count(t, s, "accounts") != 1 {
		t.Fatalf("account = %+v, claimed id %s", account, row.AccountID)
	}
	replay := call(s, "POST", "/v1/slack/accounts", "k", body("alice"))
	if replay.Code != http.StatusCreated || !bytes.Equal(replay.Body.Bytes(), recovered.Body.Bytes()) {
		t.Fatalf("replay = %d %s", replay.Code, replay.Body.String())
	}
	if st := getStats(t, s); st.Takeovers != 1 || st.Replayed != 1 || st.Created != 1 {
		t.Fatalf("stats = %+v", st)
	}
}

func TestCrashAfterAccountInsertBeforeResponseStored(t *testing.T) {
	s := newTestService(t, Config{})
	s.cfg.Hooks.CrashAt = crashOnce("after_account_insert")
	server := httptest.NewServer(s)
	defer server.Close()
	if _, _, err := post(t, server.URL, "/v1/slack/accounts", "k", body("alice")); err == nil {
		t.Fatal("crashed request returned a response")
	}
	row := fetchRow(t, s, "k")
	if row.State != "processing" || count(t, s, "accounts") != 1 {
		t.Fatalf("row = %+v, accounts = %d", row, count(t, s, "accounts"))
	}
	assertInProgress(t, call(s, "POST", "/v1/slack/accounts", "k", body("alice")))
	age(t, s, "k", time.Hour)
	recovered := call(s, "POST", "/v1/slack/accounts", "k", body("alice"))
	assertStatus(t, recovered, http.StatusCreated)
	var account Account
	if err := json.Unmarshal(recovered.Body.Bytes(), &account); err != nil {
		t.Fatal(err)
	}
	if account.ExternalRef != row.AccountID || count(t, s, "accounts") != 1 {
		t.Fatalf("account = %+v, claimed id %s", account, row.AccountID)
	}
	replay := call(s, "POST", "/v1/slack/accounts", "k", body("alice"))
	if replay.Code != http.StatusCreated || !bytes.Equal(replay.Body.Bytes(), recovered.Body.Bytes()) {
		t.Fatalf("replay = %d %s", replay.Code, replay.Body.String())
	}
	if st := getStats(t, s); st.Takeovers != 1 || st.Replayed != 1 || st.Created != 1 {
		t.Fatalf("stats = %+v", st)
	}
}

// A claim younger than ProcessingTimeout is respected, an older one is recovered.
func TestProcessingTimeoutBoundary(t *testing.T) {
	s := newTestService(t, Config{ProcessingTimeout: 10 * time.Second})
	id := seedStuck(t, s, "k", "slack", "alice", 2*time.Second)
	assertInProgress(t, call(s, "POST", "/v1/slack/accounts", "k", body("alice")))
	if count(t, s, "accounts") != 0 || getStats(t, s).Takeovers != 0 {
		t.Fatal("a young claim was taken over")
	}
	age(t, s, "k", 11*time.Second)
	w := call(s, "POST", "/v1/slack/accounts", "k", body("alice"))
	assertStatus(t, w, http.StatusCreated)
	if !strings.Contains(w.Body.String(), id) {
		t.Fatalf("body = %s, want account %s", w.Body.String(), id)
	}
}

func TestStuckClaimConcurrentRequestsCreateOneAccount(t *testing.T) {
	s := newTestService(t, Config{})
	other, err := New(testContext(t), s.db, Config{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	id := seedStuck(t, s, "k", "slack", "alice", time.Hour)
	const n = 50
	start := make(chan struct{})
	responses := make(chan *httptest.ResponseRecorder, n)
	var wg sync.WaitGroup
	for i := range n {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			handler := s
			if i%2 == 1 {
				handler = other
			}
			responses <- call(handler, "POST", "/v1/slack/accounts", "k", body("alice"))
		}()
	}
	close(start)
	wg.Wait()
	close(responses)
	var created []byte
	for w := range responses {
		switch w.Code {
		case http.StatusCreated:
			if created == nil {
				created = bytes.Clone(w.Body.Bytes())
			}
			if !bytes.Equal(created, w.Body.Bytes()) {
				t.Fatal("responses differ")
			}
		case http.StatusConflict:
			if !strings.Contains(w.Body.String(), `"in progress"`) {
				t.Fatal(w.Body.String())
			}
		default:
			t.Fatalf("unexpected %d %s", w.Code, w.Body.String())
		}
	}
	if created == nil || !strings.Contains(string(created), id) {
		t.Fatalf("no request completed with the claimed account %s: %s", id, created)
	}
	if count(t, s, "accounts") != 1 {
		t.Fatalf("accounts = %d, want 1", count(t, s, "accounts"))
	}
	if st := getStats(t, s); st.Takeovers != 1 {
		t.Fatalf("takeovers = %d, want 1", st.Takeovers)
	}
}

// Both takeovers read the stale row before either runs the compare-and-set; exactly one may win.
func TestConcurrentTakeoversOneWins(t *testing.T) {
	var arrived sync.WaitGroup
	arrived.Add(2)
	barrier := func() {
		arrived.Done()
		arrived.Wait()
	}
	s := newTestService(t, Config{Hooks: Hooks{BeforeTakeover: barrier}})
	seedStuck(t, s, "k", "slack", "alice", time.Hour)
	results := make(chan *httptest.ResponseRecorder, 2)
	for range 2 {
		go func() { results <- call(s, "POST", "/v1/slack/accounts", "k", body("alice")) }()
	}
	var created, conflicts int
	for range 2 {
		w := <-results
		switch w.Code {
		case http.StatusCreated:
			created++
		case http.StatusConflict:
			if !strings.Contains(w.Body.String(), `"in progress"`) {
				t.Fatal(w.Body.String())
			}
			conflicts++
		default:
			t.Fatalf("unexpected %d %s", w.Code, w.Body.String())
		}
	}
	if created != 1 || conflicts != 1 {
		t.Fatalf("created=%d conflicts=%d, want 1 and 1", created, conflicts)
	}
	if st := getStats(t, s); st.Takeovers != 1 || count(t, s, "accounts") != 1 {
		t.Fatalf("stats = %+v", st)
	}
}

func TestStuckClaimDifferentBodyStill422(t *testing.T) {
	s := newTestService(t, Config{})
	seedStuck(t, s, "young", "slack", "alice", time.Second)
	seedStuck(t, s, "old", "slack", "alice", time.Hour)
	for _, key := range []string{"young", "old"} {
		assertStatus(t, call(s, "POST", "/v1/slack/accounts", key, body("bob")), http.StatusUnprocessableEntity)
	}
	if count(t, s, "accounts") != 0 || getStats(t, s).Takeovers != 0 {
		t.Fatal("a mismatched body recovered a claim")
	}
	if fetchRow(t, s, "old").State != "processing" {
		t.Fatal("mismatched body completed the claim")
	}
}

func TestStuckClaimWithAccountUnderAnotherKeyIsConflict(t *testing.T) {
	s := newTestService(t, Config{})
	assertStatus(t, call(s, "POST", "/v1/slack/accounts", "first", body("alice")), http.StatusCreated)
	seedStuck(t, s, "stuck", "slack", "alice", time.Hour)
	w := call(s, "POST", "/v1/slack/accounts", "stuck", body("alice"))
	assertStatus(t, w, http.StatusConflict)
	if w.Body.String() != `{"error":"account exists"}` {
		t.Fatal(w.Body.String())
	}
	replay := call(s, "POST", "/v1/slack/accounts", "stuck", body("alice"))
	if replay.Code != http.StatusConflict || replay.Body.String() != w.Body.String() {
		t.Fatalf("replay = %d %s", replay.Code, replay.Body.String())
	}
	if count(t, s, "accounts") != 1 {
		t.Fatal("second account created")
	}
}

// The row completes between the stale read and the takeover: the takeover must not touch a done row.
func TestTakeoverDoesNotTouchCompletedRow(t *testing.T) {
	var s *Service
	var once sync.Once
	s = newTestService(t, Config{Hooks: Hooks{BeforeTakeover: func() {
		once.Do(func() {
			_, err := s.db.Collection("requests").UpdateOne(testContext(t), bson.M{"key": "k"},
				bson.M{"$set": bson.M{"state": "done", "status": 201, "body": []byte(`{"original":true}`)}})
			if err != nil {
				t.Error(err)
			}
		})
	}}})
	seedStuck(t, s, "k", "slack", "alice", time.Hour)
	assertInProgress(t, call(s, "POST", "/v1/slack/accounts", "k", body("alice")))
	row := fetchRow(t, s, "k")
	if row.State != "done" || string(row.Body) != `{"original":true}` || row.Claim != 0 {
		t.Fatalf("row = %+v", row)
	}
	if getStats(t, s).Takeovers != 0 || count(t, s, "accounts") != 0 {
		t.Fatal("takeover proceeded on a completed row")
	}
}

// A former owner that stalled past the timeout cannot overwrite the result of the request that took over.
func TestStalledOriginalCannotOverwriteTakeover(t *testing.T) {
	stalled := make(chan struct{})
	release := make(chan struct{})
	var first atomic.Bool
	s := newTestService(t, Config{Hooks: Hooks{CrashAt: func(stage string) bool {
		if stage == "after_account_insert" && first.CompareAndSwap(false, true) {
			close(stalled)
			<-release
		}
		return false
	}}})
	original := make(chan *httptest.ResponseRecorder, 1)
	go func() { original <- call(s, "POST", "/v1/slack/accounts", "k", body("alice")) }()
	<-stalled
	age(t, s, "k", time.Hour)
	taken := call(s, "POST", "/v1/slack/accounts", "k", body("alice"))
	assertStatus(t, taken, http.StatusCreated)
	close(release)
	assertStatus(t, <-original, http.StatusServiceUnavailable)
	row := fetchRow(t, s, "k")
	if row.State != "done" || row.Claim != 1 || !bytes.Equal(row.Body, taken.Body.Bytes()) {
		t.Fatalf("row = %+v", row)
	}
	if count(t, s, "accounts") != 1 {
		t.Fatal("duplicate account")
	}
}
