package worker

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"launchops/provisioner/internal/connectors"
	"launchops/provisioner/internal/jobs"
	"launchops/provisioner/internal/mocksaas"

	"go.mongodb.org/mongo-driver/v2/bson"
)

const (
	google = "google"
	slack  = "slack"
)

func countEvents(evs []event, typ string) int {
	n := 0
	for _, ev := range evs {
		if ev.Type == typ {
			n++
		}
	}
	return n
}

func errorsOf(evs []event, typ string) []string {
	var out []string
	for _, ev := range evs {
		if ev.Type == typ {
			s, _ := ev.Detail["error"].(string)
			out = append(out, s)
		}
	}
	return out
}

// Three replicas of 4 workers share one store and process 12 jobs: each job creates exactly one account.
func TestThreePoolsTwelveJobsOneAccountEach(t *testing.T) {
	policy := testPolicy(2 * time.Second)
	e := newEnv(t, policy)
	type key struct {
		id        bson.ObjectID
		connector string
	}
	var keys []key
	for i := range 6 {
		user := fmt.Sprintf("user%d", i)
		id := e.seed(t, jobSpec{google, user}, jobSpec{slack, user})
		keys = append(keys, key{id, google}, key{id, slack})
	}
	pools := []*running{
		start(t, e.pool("A", 4, policy, nil)),
		start(t, e.pool("B", 4, policy, nil)),
		start(t, e.pool("C", 4, policy, nil)),
	}
	for _, k := range keys {
		e.waitStatus(t, k.id, k.connector, jobs.StatusSucceeded, 20*time.Second)
	}
	for _, p := range pools {
		if err, _ := p.stop(t, 10*time.Second); err != nil {
			t.Fatalf("Run = %v, want nil", err)
		}
	}
	assertNoLeak(t, e.conn)

	accounts := e.accounts(t)
	if len(accounts) != 12 {
		t.Fatalf("accounts = %d, want 12", len(accounts))
	}
	byOwner := map[string]string{}
	for _, a := range accounts {
		byOwner[a.Connector+"/"+a.Username] = a.ExternalRef
	}
	for _, k := range keys {
		j := e.job(t, k.id, k.connector)
		if j.Attempts != 1 || j.Fence != 1 {
			t.Errorf("%s:%s attempts=%d fence=%d, want 1/1", k.id.Hex(), k.connector, j.Attempts, j.Fence)
		}
		if j.ExternalRef == nil || *j.ExternalRef != byOwner[k.connector+"/"+j.Payload.Username] {
			t.Errorf("%s:%s external_ref = %v, want the account's %q", k.id.Hex(), k.connector, j.ExternalRef, byOwner[k.connector+"/"+j.Payload.Username])
		}
		if n := countEvents(e.jobEvents(t, k.id, k.connector), jobs.EventSucceeded); n != 1 {
			t.Errorf("%s:%s succeeded events = %d, want 1", k.id.Hex(), k.connector, n)
		}
	}
	if s := e.stats(t); s.Replayed != 0 || s.InProgressConflicts != 0 {
		t.Errorf("stats = %+v, want no replays and no in-progress conflicts (each job called once)", s)
	}
	if n := len(e.logs.attempts(t, "succeeded")); n != 12 {
		t.Errorf("succeeded attempt log lines = %d, want 12", n)
	}
}

// mock-saas commits the account and closes the connection without a response. The retry replays the
// stored response under the same key: one account, and the job keeps the original external_ref.
func TestDropAfterCommitRetriesToOriginalRef(t *testing.T) {
	policy := testPolicy(2 * time.Second)
	e := newEnv(t, policy)
	id := e.seed(t, jobSpec{google, "ada"})
	e.setFaults(t, `{"drop_after_commit_rate":1}`)
	r := start(t, e.pool("A", 1, policy, nil))
	waitFor(t, 10*time.Second, "a dropped commit", func() bool { return e.stats(t).DropAfterCommitFired >= 1 })
	e.setFaults(t, `{"drop_after_commit_rate":0}`)
	j := e.waitStatus(t, id, google, jobs.StatusSucceeded, 10*time.Second)
	if err, _ := r.stop(t, 10*time.Second); err != nil {
		t.Fatal(err)
	}
	assertNoLeak(t, e.conn)

	accounts := e.accounts(t)
	if len(accounts) != 1 {
		t.Fatalf("accounts = %d, want 1", len(accounts))
	}
	if j.ExternalRef == nil || *j.ExternalRef != accounts[0].ExternalRef {
		t.Fatalf("external_ref = %v, want the account created on the dropped attempt %q", j.ExternalRef, accounts[0].ExternalRef)
	}
	if j.Attempts != 2 || j.Fence != 2 {
		t.Fatalf("attempts=%d fence=%d, want 2/2 (dropped, then replayed)", j.Attempts, j.Fence)
	}
	s := e.stats(t)
	if s.DropAfterCommitFired != 1 || s.Replayed < 1 {
		t.Fatalf("stats = %+v, want one drop and at least one replay", s)
	}
	evs := e.jobEvents(t, id, google)
	retries := errorsOf(evs, jobs.EventRetry)
	if len(retries) != 1 || !strings.Contains(retries[0], "EOF") {
		t.Fatalf("retry events = %q, want one connection-closed (EOF) error", retries)
	}
	if countEvents(evs, jobs.EventSucceeded) != 1 {
		t.Fatalf("events = %+v, want one succeeded", evs)
	}
}

// The first attempt is interrupted after mock-saas committed the account but before it stored the response, leaving
// the key `processing`. Retries get 409 "in progress" until the claim is stale, then mock-saas reconciles it:
// the job succeeds with the one account that was already created.
func TestInterruptedClaimRecoveredThroughRetries(t *testing.T) {
	policy := testPolicy(2 * time.Second)
	policy.MaxAttempts = 20
	var crashed atomic.Bool
	e := newEnvMock(t, policy, mocksaas.Config{
		ProcessingTimeout: 150 * time.Millisecond,
		Hooks: mocksaas.Hooks{CrashAt: func(stage string) bool {
			return stage == "after_account_insert" && crashed.CompareAndSwap(false, true)
		}},
	})
	id := e.seed(t, jobSpec{google, "ada"})
	r := start(t, e.pool("A", 1, policy, nil))
	j := e.waitStatus(t, id, google, jobs.StatusSucceeded, 15*time.Second)
	if err, _ := r.stop(t, 10*time.Second); err != nil {
		t.Fatal(err)
	}
	assertNoLeak(t, e.conn)

	accounts := e.accounts(t)
	if len(accounts) != 1 {
		t.Fatalf("accounts = %d, want 1", len(accounts))
	}
	if j.ExternalRef == nil || *j.ExternalRef != accounts[0].ExternalRef {
		t.Fatalf("external_ref = %v, want the account created on the interrupted attempt %q", j.ExternalRef, accounts[0].ExternalRef)
	}
	if j.Attempts < 2 {
		t.Fatalf("attempts = %d, want at least 2", j.Attempts)
	}
	evs := e.jobEvents(t, id, google)
	retries := errorsOf(evs, jobs.EventRetry)
	if len(retries) < 1 || !strings.Contains(retries[0], "EOF") {
		t.Fatalf("retry events = %q, want the first to be the dropped connection (EOF)", retries)
	}
	if s := e.stats(t); s.Takeovers != 1 {
		t.Fatalf("takeovers = %d, want 1", s.Takeovers)
	}
	if countEvents(evs, jobs.EventSucceeded) != 1 {
		t.Fatalf("events = %+v, want one succeeded", evs)
	}
}

// A hang longer than CallTimeout is a retryable timeout; once the fault is off the job succeeds.
func TestHangBeyondCallTimeoutRetriesThenSucceeds(t *testing.T) {
	policy := testPolicy(300 * time.Millisecond)
	e := newEnv(t, policy)
	id := e.seed(t, jobSpec{google, "ada"})
	e.setFaults(t, `{"hang_rate":1}`)
	r := start(t, e.pool("A", 1, policy, nil))
	waitFor(t, 10*time.Second, "a timed-out attempt", func() bool {
		return len(errorsOf(e.jobEvents(t, id, google), jobs.EventRetry)) >= 1
	})
	e.setFaults(t, `{"hang_rate":0}`)
	j := e.waitStatus(t, id, google, jobs.StatusSucceeded, 10*time.Second)
	if err, _ := r.stop(t, 10*time.Second); err != nil {
		t.Fatal(err)
	}
	assertNoLeak(t, e.conn)

	retries := errorsOf(e.jobEvents(t, id, google), jobs.EventRetry)
	if len(retries) < 1 || !strings.Contains(retries[0], "timeout") {
		t.Fatalf("retry events = %q, want a timeout first", retries)
	}
	if j.Attempts < 2 || j.Attempts != len(retries)+1 {
		t.Fatalf("attempts = %d with %d retries, want retries+1 and at least 2", j.Attempts, len(retries))
	}
	if n := len(e.accounts(t)); n != 1 {
		t.Fatalf("accounts = %d, want 1", n)
	}
	if s := e.stats(t); s.HangFired < 1 {
		t.Fatalf("stats = %+v, want the hang fault fired", s)
	}
}

// A permanent 4xx makes the job dead on its first attempt, with no retry.
func TestPermanent4xxDeadWithoutRetry(t *testing.T) {
	policy := testPolicy(2 * time.Second)
	e := newEnv(t, policy)
	invalid := e.seed(t, jobSpec{google, ""}) // mock-saas: 400, username required
	e.createDirect(t, slack, "taken")
	conflict := e.seed(t, jobSpec{slack, "taken"}) // mock-saas: 409 account exists (another key)
	r := start(t, e.pool("A", 2, policy, nil))
	for _, c := range []struct {
		id        bson.ObjectID
		connector string
		status    string
	}{{invalid, google, "HTTP 400"}, {conflict, slack, "HTTP 409"}} {
		j := e.waitStatus(t, c.id, c.connector, jobs.StatusDead, 10*time.Second)
		if j.Attempts != 1 || j.LastError == nil || !strings.Contains(*j.LastError, c.status) {
			t.Errorf("%s: attempts=%d last_error=%v, want 1 attempt and %s", c.connector, j.Attempts, j.LastError, c.status)
		}
		evs := e.jobEvents(t, c.id, c.connector)
		if countEvents(evs, jobs.EventDead) != 1 || countEvents(evs, jobs.EventRetry) != 0 {
			t.Errorf("%s: events = %+v, want one dead and no retry", c.connector, evs)
		}
	}
	time.Sleep(200 * time.Millisecond) // several poll intervals: dead jobs are never claimed again
	if err, _ := r.stop(t, 10*time.Second); err != nil {
		t.Fatal(err)
	}
	assertNoLeak(t, e.conn)
	for _, c := range []struct {
		id        bson.ObjectID
		connector string
	}{{invalid, google}, {conflict, slack}} {
		if j := e.job(t, c.id, c.connector); j.Status != jobs.StatusDead || j.Attempts != 1 || j.Fence != 1 {
			t.Errorf("%s after idle polling: %+v, want dead with 1 attempt", c.connector, j)
		}
	}
	if n := len(e.logs.attempts(t, "dead")); n != 2 {
		t.Errorf("dead attempt log lines = %d, want 2", n)
	}
}

// 409 "in progress" is retried until the request completes.
func TestInProgressConflictRetried(t *testing.T) {
	policy := testPolicy(2 * time.Second)
	e := newEnv(t, policy)
	id := e.seed(t, jobSpec{google, "ada"})
	var posts atomic.Int32
	// The exact response mock-saas gives while the same key is still processing; producing that state for
	// real needs a second request in flight inside mock-saas.
	e.setIntercept(func(w http.ResponseWriter, r *http.Request, next http.Handler) {
		if r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, "/v1/") && posts.Add(1) <= 2 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			_, _ = io.WriteString(w, `{"error":"in progress"}`)
			return
		}
		next.ServeHTTP(w, r)
	})
	r := start(t, e.pool("A", 1, policy, nil))
	j := e.waitStatus(t, id, google, jobs.StatusSucceeded, 10*time.Second)
	if err, _ := r.stop(t, 10*time.Second); err != nil {
		t.Fatal(err)
	}
	assertNoLeak(t, e.conn)
	if j.Attempts != 3 {
		t.Fatalf("attempts = %d, want 3", j.Attempts)
	}
	retries := errorsOf(e.jobEvents(t, id, google), jobs.EventRetry)
	if len(retries) != 2 || !strings.Contains(retries[0], "in progress") || !strings.Contains(retries[1], "in progress") {
		t.Fatalf("retry events = %q, want two in-progress retries", retries)
	}
	if n := len(e.accounts(t)); n != 1 {
		t.Fatalf("accounts = %d, want 1", n)
	}
}

// A worker killed mid-call (drain timeout 0) leaves its job running; a second pool reclaims it after the
// lease and succeeds; the first worker's late finish is fence-rejected and changes nothing.
func TestKilledMidCallReclaimedAfterLease(t *testing.T) {
	policy := testPolicy(2 * time.Second)
	e := newEnv(t, policy)
	id := e.seed(t, jobSpec{google, "ada"})
	g := newGate(t)
	e.setIntercept(g.intercept)
	recA := &recordingStore{Store: e.store}
	poolA := e.pool("A", 1, policy, recA)
	poolA.DrainTimeout = 0
	a := start(t, poolA)
	g.waitEntered(t)
	err, took := a.stop(t, 5*time.Second)
	if !errors.Is(err, ErrDrainTimeout) {
		t.Fatalf("Run = %v, want ErrDrainTimeout", err)
	}
	if took > time.Second {
		t.Fatalf("Run took %v to return with drain timeout 0", took)
	}
	assertNoLeak(t, e.conn)
	claimsA, _ := recA.snapshot()
	if len(claimsA) != 1 {
		t.Fatalf("pool A claims = %d, want 1", len(claimsA))
	}
	j := e.job(t, id, google)
	if j.Status != jobs.StatusRunning || j.Fence != 1 || j.LeaseOwner == nil || *j.LeaseOwner != "A/0" {
		t.Fatalf("after kill: %+v, want running, fence 1, lease_owner A/0", j)
	}
	if n := len(e.logs.attempts(t, "abandoned")); n != 1 {
		t.Fatalf("abandoned log lines = %d, want 1", n)
	}

	// Pool B runs on a clock past A's lease.
	poolB := e.pool("B", 1, policy, nil)
	poolB.Now = shifted(policy)
	b := start(t, poolB)
	j = e.waitStatus(t, id, google, jobs.StatusSucceeded, 10*time.Second)
	if err, _ := b.stop(t, 10*time.Second); err != nil {
		t.Fatal(err)
	}
	if j.Fence != 2 || j.Attempts != 2 || *j.LeaseOwner != "B/0" {
		t.Fatalf("after reclaim: fence=%d attempts=%d owner=%v, want 2/2/B/0", j.Fence, j.Attempts, *j.LeaseOwner)
	}
	accounts := e.accounts(t)
	if len(accounts) != 1 || *j.ExternalRef != accounts[0].ExternalRef {
		t.Fatalf("accounts = %+v, job ref %v: want exactly one, matching", accounts, *j.ExternalRef)
	}
	before := e.load(t, id)

	// A's late finish under its stale fence.
	if err := e.store.Succeed(testContext(t), claimsA[0], "late-ref"); !errors.Is(err, jobs.ErrFenceRejected) {
		t.Fatalf("late Succeed = %v, want ErrFenceRejected", err)
	}
	if err := e.store.RecordFailure(testContext(t), claimsA[0], "late", true, policy, time.Now()); !errors.Is(err, jobs.ErrFenceRejected) {
		t.Fatalf("late RecordFailure = %v, want ErrFenceRejected", err)
	}
	after := e.load(t, id)
	ja := after.Provisioning[0]
	if ja.Status != jobs.StatusSucceeded || *ja.ExternalRef != *j.ExternalRef || ja.Fence != 2 || len(after.Events) != len(before.Events) {
		t.Fatalf("late finish changed the job: %+v (events %d -> %d)", ja, len(before.Events), len(after.Events))
	}
	assertNoLeak(t, e.conn)
}

// A stalled worker whose call completes after another pool reclaimed and finished the job: the worker's
// own finish is fence-rejected, logged as such, and Run still exits cleanly.
func TestStalledWorkerLateFinishFenceRejected(t *testing.T) {
	policy := testPolicy(3 * time.Second)
	e := newEnv(t, policy)
	id := e.seed(t, jobSpec{google, "ada"})
	g := newGate(t)
	e.setIntercept(g.intercept)
	a := start(t, e.pool("A", 1, policy, nil))
	g.waitEntered(t)

	poolB := e.pool("B", 1, policy, nil)
	poolB.Now = shifted(policy)
	b := start(t, poolB)
	j := e.waitStatus(t, id, google, jobs.StatusSucceeded, 10*time.Second)
	if err, _ := b.stop(t, 10*time.Second); err != nil {
		t.Fatal(err)
	}
	before := e.load(t, id)

	g.open() // A's request now reaches mock-saas and replays B's stored response
	waitFor(t, 10*time.Second, "A's fence_rejected log line", func() bool {
		for _, rec := range e.logs.attempts(t, "fence_rejected") {
			if rec["worker_id"] == "A/0" && rec["fence"] == float64(1) {
				return true
			}
		}
		return false
	})
	if err, _ := a.stop(t, 10*time.Second); err != nil {
		t.Fatalf("Run = %v, want nil: fence_rejected is not an error exit", err)
	}
	assertNoLeak(t, e.conn)

	after := e.load(t, id)
	ja := after.Provisioning[0]
	if ja.Status != jobs.StatusSucceeded || *ja.ExternalRef != *j.ExternalRef || ja.Fence != 2 || len(after.Events) != len(before.Events) {
		t.Fatalf("stale finish changed the job: %+v (events %d -> %d)", ja, len(before.Events), len(after.Events))
	}
	if n := len(e.accounts(t)); n != 1 {
		t.Fatalf("accounts = %d, want 1", n)
	}
}

// Graceful shutdown: the in-flight job finishes and is recorded; nothing is claimed after cancel.
func TestGracefulShutdownDrainsInFlight(t *testing.T) {
	policy := testPolicy(2 * time.Second)
	e := newEnv(t, policy)
	first := e.seed(t, jobSpec{google, "ada"})
	g := newGate(t)
	e.setIntercept(g.intercept)
	rec := &recordingStore{Store: e.store}
	p := e.pool("A", 2, policy, rec)
	p.DrainTimeout = 5 * time.Second
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- p.Run(ctx) }()
	g.waitEntered(t)

	cancelledAt := time.Now()
	cancel()
	second := e.seed(t, jobSpec{slack, "bob"}) // due now, but shutdown has begun
	time.Sleep(300 * time.Millisecond)         // several poll intervals
	select {
	case err := <-done:
		t.Fatalf("Run returned %v before the in-flight job finished", err)
	default:
	}
	g.open()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("Run = %v, want nil after a clean drain", err)
		}
	case <-time.After(p.DrainTimeout):
		t.Fatal("Run did not return within the drain timeout")
	}
	assertNoLeak(t, e.conn)

	j := e.job(t, first, google)
	if j.Status != jobs.StatusSucceeded || j.ExternalRef == nil {
		t.Fatalf("in-flight job = %+v, want succeeded with a ref", j)
	}
	if n := len(e.accounts(t)); n != 1 {
		t.Fatalf("accounts = %d, want 1", n)
	}
	if j2 := e.job(t, second, slack); j2.Status != jobs.StatusPending || j2.Attempts != 0 || j2.Fence != 0 {
		t.Fatalf("job inserted after cancel = %+v, want untouched pending", j2)
	}
	claims, claimCalls := rec.snapshot()
	if len(claims) != 1 {
		t.Fatalf("claims = %d, want only the in-flight one", len(claims))
	}
	for _, at := range claimCalls {
		if at.After(cancelledAt) {
			t.Fatalf("a claim started %v after cancel", at.Sub(cancelledAt))
		}
	}
	if n := len(e.logs.attempts(t, "succeeded")); n != 1 {
		t.Fatalf("succeeded log lines = %d, want 1", n)
	}
}

// Run refuses a bad configuration before starting any goroutine.
func TestPoolValidation(t *testing.T) {
	policy := testPolicy(time.Second)
	conn, err := connectors.NewHTTP(connectors.HTTPConfig{BaseURL: "http://127.0.0.1:1", Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	valid := func() Pool {
		return Pool{Store: &recordingStore{}, Connector: conn, Policy: policy, WorkerID: "w", Workers: 1}
	}
	for name, mutate := range map[string]func(*Pool){
		"no store":          func(p *Pool) { p.Store = nil },
		"no connector":      func(p *Pool) { p.Connector = nil },
		"no worker id":      func(p *Pool) { p.WorkerID = "" },
		"zero workers":      func(p *Pool) { p.Workers = 0 },
		"negative drain":    func(p *Pool) { p.DrainTimeout = -time.Second },
		"poll max < min":    func(p *Pool) { p.PollMin, p.PollMax = time.Second, time.Millisecond },
		"lease too short":   func(p *Pool) { p.Policy.Lease = p.Policy.CallTimeout + jobs.LeaseMargin },
		"zero max attempts": func(p *Pool) { p.Policy.MaxAttempts = 0 },
	} {
		p := valid()
		mutate(&p)
		if err := p.Run(context.Background()); err == nil {
			t.Errorf("%s: Run = nil, want a validation error", name)
		}
	}
}
