package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"launchops/provisioner/internal/connectors"
	"launchops/provisioner/internal/jobs"
	"launchops/provisioner/internal/mocksaas"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const collName = "workflow_instances"

// testPolicy keeps backoff tiny. Lease covers claim, call, finish and margin, so lease-expiry
// tests shift a pool's clock instead of waiting.
func testPolicy(callTimeout time.Duration) jobs.Policy {
	return jobs.Policy{MaxAttempts: 5, Base: 10 * time.Millisecond, Cap: 50 * time.Millisecond,
		CallTimeout: callTimeout, StoreOpTimeout: 5 * time.Second, FinishTimeout: DefaultFinishTimeout,
		Lease: 5*time.Second + callTimeout + DefaultFinishTimeout + jobs.LeaseMargin + time.Second}
}

// interceptor sits in front of the mock-saas handler. It sees every request; next is mock-saas.
type interceptor func(w http.ResponseWriter, r *http.Request, next http.Handler)

type env struct {
	coll      *mongo.Collection
	mockDB    *mongo.Database
	store     *jobs.Store
	srv       *httptest.Server
	conn      *connectors.HTTP
	logs      *syncBuffer
	logger    *slog.Logger
	intercept atomic.Pointer[interceptor]
	// admin makes test-side calls; no keep-alive, so it leaves no connection goroutines for assertNoLeak.
	admin *http.Client
}

type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

// lines returns the JSON log records written so far.
func (b *syncBuffer) lines(t *testing.T) []map[string]any {
	t.Helper()
	b.mu.Lock()
	data := append([]byte(nil), b.buf.Bytes()...)
	b.mu.Unlock()
	var out []map[string]any
	for _, line := range bytes.Split(data, []byte("\n")) {
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		var rec map[string]any
		if err := json.Unmarshal(line, &rec); err != nil {
			t.Fatalf("log line is not JSON: %q", line)
		}
		out = append(out, rec)
	}
	return out
}

// attempts returns the pool's "job attempt" records, optionally only those with the given outcome.
func (b *syncBuffer) attempts(t *testing.T, outcome string) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, rec := range b.lines(t) {
		if rec["msg"] == "job attempt" && (outcome == "" || rec["outcome"] == outcome) {
			out = append(out, rec)
		}
	}
	return out
}

func testContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)
	return ctx
}

func newEnv(t *testing.T, policy jobs.Policy) *env {
	t.Helper()
	uri := os.Getenv("MONGO_URI")
	if uri == "" {
		t.Fatal("MONGO_URI is required: worker tests run against a real MongoDB and never skip")
	}
	client, err := mongo.Connect(options.Client().ApplyURI(uri).
		SetConnectTimeout(5 * time.Second).SetServerSelectionTimeout(5 * time.Second))
	if err != nil {
		t.Fatal(err)
	}
	suffix := bson.NewObjectID().Hex()
	jobsDB := client.Database("worker_test_" + suffix)
	mockDB := client.Database("worker_mocksaas_" + suffix)
	e := &env{coll: jobsDB.Collection(collName), mockDB: mockDB, logs: &syncBuffer{},
		admin: &http.Client{Transport: &http.Transport{DisableKeepAlives: true}, Timeout: 10 * time.Second}}
	e.logger = slog.New(slog.NewJSONHandler(e.logs, nil))
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		for _, db := range []*mongo.Database{jobsDB, mockDB} {
			if err := db.Drop(ctx); err != nil {
				t.Error(err)
			}
		}
		if err := client.Disconnect(ctx); err != nil {
			t.Error(err)
		}
	})
	if _, err := e.coll.Indexes().CreateOne(testContext(t), mongo.IndexModel{
		Keys: bson.D{{Key: "provisioning.status", Value: 1}, {Key: "provisioning.next_attempt_at", Value: 1}},
	}); err != nil {
		t.Fatal(err)
	}
	e.store, err = jobs.NewStore(client, jobsDB.Name(), collName, jobs.StoreConfig{
		Policy: policy, OpTimeout: 5 * time.Second, Logger: e.logger,
	})
	if err != nil {
		t.Fatal(err)
	}
	mock, err := mocksaas.New(testContext(t), mockDB, mocksaas.Config{HangDuration: 3 * time.Second},
		slog.New(slog.NewJSONHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	e.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if f := e.intercept.Load(); f != nil {
			(*f)(w, r, mock)
			return
		}
		mock.ServeHTTP(w, r)
	}))
	t.Cleanup(e.srv.Close)
	e.conn, err = connectors.NewHTTP(connectors.HTTPConfig{BaseURL: e.srv.URL, Timeout: policy.CallTimeout})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(e.conn.CloseIdleConnections)
	return e
}

func (e *env) setIntercept(f interceptor) { e.intercept.Store(&f) }

func (e *env) pool(id string, workers int, policy jobs.Policy, store Store) *Pool {
	if store == nil {
		store = e.store
	}
	return &Pool{Store: store, Connector: e.conn, Policy: policy, WorkerID: id, Workers: workers, Logger: e.logger,
		DrainTimeout: 5 * time.Second, PollMin: 10 * time.Millisecond, PollMax: 50 * time.Millisecond}
}

// shifted returns a clock that runs past every lease taken on the real clock.
func shifted(policy jobs.Policy) func() time.Time {
	return func() time.Time { return time.Now().Add(policy.Lease + time.Second) }
}

type jobSpec struct{ connector, username string }

// seed inserts one instance shaped like Django writes it (int32 counters), with pending jobs due now.
func (e *env) seed(t *testing.T, specs ...jobSpec) bson.ObjectID {
	t.Helper()
	id := bson.NewObjectID()
	now := time.Now().UTC()
	var prov bson.A
	for _, s := range specs {
		prov = append(prov, bson.D{
			{Key: "connector", Value: s.connector},
			{Key: "idempotency_key", Value: id.Hex() + ":" + s.connector},
			{Key: "payload", Value: bson.D{{Key: "username", Value: s.username}, {Key: "display_name", Value: "Test " + s.username}, {Key: "department", Value: "Eng"}}},
			{Key: "status", Value: "pending"}, {Key: "attempts", Value: int32(0)}, {Key: "fence", Value: int32(0)},
			{Key: "lease_owner", Value: nil}, {Key: "lease_until", Value: nil}, {Key: "next_attempt_at", Value: now.Add(-time.Second)},
			{Key: "last_error", Value: nil}, {Key: "external_ref", Value: nil}, {Key: "override_reason", Value: nil},
			{Key: "updated_at", Value: now},
		})
	}
	doc := bson.D{
		{Key: "_id", Value: id}, {Key: "blueprint_version", Value: int32(2)}, {Key: "status", Value: "active"},
		{Key: "created_at", Value: now}, {Key: "version", Value: int32(1)},
		{Key: "steps", Value: bson.A{bson.D{{Key: "key", Value: "access"}, {Key: "status", Value: "open"}, {Key: "due_at", Value: now}, {Key: "completion", Value: nil}}}},
		{Key: "issues", Value: bson.A{}},
		{Key: "events", Value: bson.A{bson.D{{Key: "type", Value: "instance.created"}, {Key: "step_key", Value: nil},
			{Key: "actor", Value: "hr1"}, {Key: "at", Value: now}, {Key: "detail", Value: bson.D{}}}}},
		{Key: "provisioning", Value: prov},
	}
	if _, err := e.coll.InsertOne(testContext(t), doc); err != nil {
		t.Fatal(err)
	}
	return id
}

type event struct {
	Type   string `bson:"type"`
	Actor  string `bson:"actor"`
	Detail bson.M `bson:"detail"`
}

type instance struct {
	Version      int        `bson:"version"`
	Events       []event    `bson:"events"`
	Provisioning []jobs.Job `bson:"provisioning"`
}

func (e *env) load(t *testing.T, id bson.ObjectID) instance {
	t.Helper()
	var doc instance
	if err := e.coll.FindOne(testContext(t), bson.M{"_id": id}).Decode(&doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

func (e *env) job(t *testing.T, id bson.ObjectID, connector string) jobs.Job {
	t.Helper()
	for _, j := range e.load(t, id).Provisioning {
		if j.Connector == connector {
			return j
		}
	}
	t.Fatalf("job %s:%s not found", id.Hex(), connector)
	return jobs.Job{}
}

// jobEvents returns the provisioning.* events for one connector, in array (event) order.
func (e *env) jobEvents(t *testing.T, id bson.ObjectID, connector string) []event {
	t.Helper()
	var out []event
	for _, ev := range e.load(t, id).Events {
		if strings.HasPrefix(ev.Type, "provisioning.") && ev.Detail["connector"] == connector {
			out = append(out, ev)
		}
	}
	return out
}

func waitFor(t *testing.T, timeout time.Duration, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out after %v waiting for %s", timeout, what)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func (e *env) waitStatus(t *testing.T, id bson.ObjectID, connector string, status jobs.Status, timeout time.Duration) jobs.Job {
	t.Helper()
	var j jobs.Job
	waitFor(t, timeout, fmt.Sprintf("%s:%s to be %s", id.Hex(), connector, status), func() bool {
		j = e.job(t, id, connector)
		return j.Status == status
	})
	return j
}

func (e *env) accounts(t *testing.T) []mocksaas.Account {
	t.Helper()
	cur, err := e.mockDB.Collection("accounts").Find(testContext(t), bson.D{})
	if err != nil {
		t.Fatal(err)
	}
	var out []mocksaas.Account
	if err := cur.All(testContext(t), &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func (e *env) stats(t *testing.T) mocksaas.Stats {
	t.Helper()
	resp, err := e.admin.Get(e.srv.URL + "/admin/stats")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var s mocksaas.Stats
	if err := json.NewDecoder(resp.Body).Decode(&s); err != nil {
		t.Fatal(err)
	}
	return s
}

func (e *env) setFaults(t *testing.T, patch string) {
	t.Helper()
	resp, err := e.admin.Post(e.srv.URL+"/admin/faults", "application/json", strings.NewReader(patch))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("set faults %s: HTTP %d %s", patch, resp.StatusCode, body)
	}
}

// createDirect creates an account in mock-saas under a key the job does not use.
func (e *env) createDirect(t *testing.T, connector, username string) {
	t.Helper()
	req, _ := http.NewRequest(http.MethodPost, e.srv.URL+"/v1/"+connector+"/accounts",
		strings.NewReader(fmt.Sprintf(`{"username":%q,"display_name":"Other","department":"Ops"}`, username)))
	req.Header.Set("Idempotency-Key", "someone-else:"+username)
	resp, err := e.admin.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("direct create: HTTP %d", resp.StatusCode)
	}
}

// running is a pool started in the background; Cleanup always stops it.
type running struct {
	cancel  context.CancelFunc
	done    chan error
	stopped bool
}

func start(t *testing.T, p *Pool) *running {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	r := &running{cancel: cancel, done: make(chan error, 1)}
	go func() { r.done <- p.Run(ctx) }()
	t.Cleanup(func() {
		if !r.stopped {
			cancel()
			select {
			case <-r.done:
			case <-time.After(30 * time.Second):
				t.Error("pool did not stop")
			}
		}
	})
	return r
}

// stop cancels the pool and returns Run's result and how long Run took to return after cancel.
func (r *running) stop(t *testing.T, within time.Duration) (error, time.Duration) {
	t.Helper()
	at := time.Now()
	r.cancel()
	select {
	case err := <-r.done:
		r.stopped = true
		return err, time.Since(at)
	case <-time.After(within):
		t.Fatalf("Run did not return within %v of cancel", within)
		return nil, 0
	}
}

// gate blocks the first account POST until released (or until the client gives up), then forwards it.
type gate struct {
	n       atomic.Int32
	entered chan struct{}
	release chan struct{}
	once    sync.Once
}

func newGate(t *testing.T) *gate {
	g := &gate{entered: make(chan struct{}), release: make(chan struct{})}
	t.Cleanup(g.open) // never leave a handler blocked, or httptest.Server.Close waits forever
	return g
}

func (g *gate) open() { g.once.Do(func() { close(g.release) }) }

func (g *gate) intercept(w http.ResponseWriter, r *http.Request, next http.Handler) {
	if r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, "/v1/") && g.n.Add(1) == 1 {
		// Read the body to EOF first: only then does net/http watch the connection and cancel
		// r.Context() when the client gives up.
		body, err := io.ReadAll(r.Body)
		if err != nil {
			return
		}
		r.Body = io.NopCloser(bytes.NewReader(body))
		close(g.entered)
		select {
		case <-g.release:
		case <-r.Context().Done():
			return
		}
	}
	next.ServeHTTP(w, r)
}

func (g *gate) waitEntered(t *testing.T) {
	t.Helper()
	select {
	case <-g.entered:
	case <-time.After(10 * time.Second):
		t.Fatal("no account request reached the gate")
	}
}

// recordingStore wraps the real store and records Candidates/Claim call times and won claims.
type recordingStore struct {
	Store
	mu         sync.Mutex
	claims     []jobs.Claim
	candidates []time.Time
	claimCalls []time.Time
}

func (s *recordingStore) Candidates(ctx context.Context, now time.Time, limit int) ([]jobs.Candidate, error) {
	s.mu.Lock()
	s.candidates = append(s.candidates, time.Now())
	s.mu.Unlock()
	return s.Store.Candidates(ctx, now, limit)
}

func (s *recordingStore) Claim(ctx context.Context, c jobs.Candidate, workerID string, now time.Time) (jobs.Claim, error) {
	s.mu.Lock()
	s.claimCalls = append(s.claimCalls, time.Now())
	s.mu.Unlock()
	claim, err := s.Store.Claim(ctx, c, workerID, now)
	if err == nil {
		s.mu.Lock()
		s.claims = append(s.claims, claim)
		s.mu.Unlock()
	}
	return claim, err
}

func (s *recordingStore) snapshot() (claims []jobs.Claim, claimCalls []time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]jobs.Claim(nil), s.claims...), append([]time.Time(nil), s.claimCalls...)
}

// assertNoLeak is a goleak-style check: once Run has returned, no pool goroutine and no client
// connection goroutine may remain.
func assertNoLeak(t *testing.T, conn *connectors.HTTP) {
	t.Helper()
	conn.CloseIdleConnections()
	markers := []string{"launchops/provisioner/internal/worker.(*Pool)", "launchops/provisioner/internal/worker.(*worker)", "net/http.(*persistConn)"}
	deadline := time.Now().Add(3 * time.Second)
	for {
		buf := make([]byte, 1<<20)
		stacks := string(buf[:runtime.Stack(buf, true)])
		var leaked []string
		for _, g := range strings.Split(stacks, "\n\n") {
			for _, m := range markers {
				if strings.Contains(g, m) {
					leaked = append(leaked, g)
					break
				}
			}
		}
		if len(leaked) == 0 {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("%d goroutine(s) leaked after Run returned:\n%s", len(leaked), strings.Join(leaked, "\n\n"))
		}
		time.Sleep(20 * time.Millisecond)
	}
}
