package jobs

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"launchops/provisioner/internal/backoff"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	collName  = "workflow_instances"
	indexName = "provisioning.status_1_provisioning.next_attempt_at_1"
	google    = "google"
	slack     = "slack"
)

// t0 is millisecond-aligned because MongoDB stores dates with millisecond precision.
var t0 = time.Date(2026, 11, 5, 9, 0, 0, 0, time.UTC)

// maxRand always draws the top of the range, so next_attempt_at = now + backoff ceiling.
type maxRand struct{}

func (maxRand) Int64N(n int64) int64 { return n - 1 }

var _ backoff.Rand = maxRand{}

type env struct {
	store  *Store
	coll   *mongo.Collection
	db     *mongo.Database
	policy Policy
	offset atomic.Int64 // Store.Now() = t0 + offset
}

func (e *env) setNow(t time.Time) { e.offset.Store(int64(t.Sub(t0))) }

func testContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)
	return ctx
}

func newEnv(t *testing.T, policy Policy) *env {
	t.Helper()
	uri := os.Getenv("MONGO_URI")
	if uri == "" {
		t.Fatal("MONGO_URI is required: job store tests run against a real MongoDB and never skip")
	}
	client, err := mongo.Connect(options.Client().ApplyURI(uri).
		SetConnectTimeout(5 * time.Second).SetServerSelectionTimeout(5 * time.Second))
	if err != nil {
		t.Fatal(err)
	}
	dbName := "jobs_test_" + bson.NewObjectID().Hex()
	db := client.Database(dbName)
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
	e := &env{db: db, coll: db.Collection(collName), policy: policy}
	if _, err := e.coll.Indexes().CreateOne(testContext(t), mongo.IndexModel{
		Keys: bson.D{{Key: "provisioning.status", Value: 1}, {Key: "provisioning.next_attempt_at", Value: 1}},
	}); err != nil {
		t.Fatal(err)
	}
	e.store, err = NewStore(client, dbName, collName, StoreConfig{
		Policy: policy, OpTimeout: 5 * time.Second, Rand: maxRand{},
		Now:    func() time.Time { return t0.Add(time.Duration(e.offset.Load())) },
		Logger: slog.New(slog.NewJSONHandler(io.Discard, nil)),
	})
	if err != nil {
		t.Fatal(err)
	}
	return e
}

// seed inserts an instance shaped like Django writes it (int32 counters, nested steps, issues, prior events)
// with two pending jobs due since t0-1m.
func (e *env) seed(t *testing.T) bson.ObjectID {
	t.Helper()
	id := bson.NewObjectID()
	created := t0.Add(-time.Hour)
	due := t0.Add(-time.Minute)
	job := func(connector string) bson.D {
		return bson.D{
			{Key: "connector", Value: connector},
			{Key: "idempotency_key", Value: id.Hex() + ":" + connector},
			{Key: "payload", Value: bson.D{{Key: "username", Value: "ada"}, {Key: "display_name", Value: "Ada L"}, {Key: "department", Value: "Eng"}}},
			{Key: "status", Value: "pending"}, {Key: "attempts", Value: int32(0)}, {Key: "fence", Value: int32(0)},
			{Key: "lease_owner", Value: nil}, {Key: "lease_until", Value: nil}, {Key: "next_attempt_at", Value: due},
			{Key: "last_error", Value: nil}, {Key: "external_ref", Value: nil}, {Key: "override_reason", Value: nil},
			{Key: "updated_at", Value: created},
		}
	}
	doc := bson.D{
		{Key: "_id", Value: id}, {Key: "blueprint_version", Value: int32(2)},
		{Key: "employee_id", Value: bson.NewObjectID()}, {Key: "manager_id", Value: bson.NewObjectID()},
		{Key: "created_by", Value: bson.NewObjectID()}, {Key: "job_title", Value: "Engineer"}, {Key: "department", Value: "Eng"},
		{Key: "start_date", Value: "2026-12-01"}, {Key: "status", Value: "active"}, {Key: "created_at", Value: created},
		{Key: "version", Value: int32(3)},
		{Key: "steps", Value: bson.A{
			bson.D{{Key: "key", Value: "contract"}, {Key: "status", Value: "done"}, {Key: "due_at", Value: created},
				{Key: "completion", Value: bson.D{{Key: "by", Value: "hr1"}, {Key: "at", Value: created}, {Key: "evidence", Value: "signed"},
					{Key: "proof", Value: nil}, {Key: "request_id", Value: "req-1"}}}},
			bson.D{{Key: "key", Value: "access"}, {Key: "status", Value: "open"}, {Key: "due_at", Value: t0}, {Key: "completion", Value: nil}},
		}},
		{Key: "issues", Value: bson.A{
			bson.D{{Key: "id", Value: "iss-1"}, {Key: "step_key", Value: "contract"}, {Key: "detail", Value: "typo"},
				{Key: "resolution", Value: nil}, {Key: "resolved_at", Value: nil}, {Key: "resolved_by", Value: nil}},
		}},
		{Key: "events", Value: bson.A{
			bson.D{{Key: "type", Value: "instance.created"}, {Key: "step_key", Value: nil}, {Key: "actor", Value: "hr1"},
				{Key: "at", Value: created}, {Key: "detail", Value: bson.D{}}},
			bson.D{{Key: "type", Value: "step.completed"}, {Key: "step_key", Value: "contract"}, {Key: "actor", Value: "hr1"},
				{Key: "at", Value: created}, {Key: "detail", Value: bson.D{}}},
		}},
		{Key: "provisioning", Value: bson.A{job(google), job(slack)}},
	}
	if _, err := e.coll.InsertOne(testContext(t), doc); err != nil {
		t.Fatal(err)
	}
	return id
}

// setJob simulates a Django/IT write to one job element.
func (e *env) setJob(t *testing.T, id bson.ObjectID, connector string, set bson.D) {
	t.Helper()
	for i := range set {
		set[i].Key = "provisioning.$[j]." + set[i].Key
	}
	res, err := e.coll.UpdateOne(testContext(t), bson.D{{Key: "_id", Value: id}}, bson.D{{Key: "$set", Value: set}},
		options.UpdateOne().SetArrayFilters([]any{bson.D{{Key: "j.connector", Value: connector}}}))
	if err != nil || res.ModifiedCount != 1 {
		t.Fatalf("direct update: %v (modified %d)", err, res.ModifiedCount)
	}
}

func (e *env) raw(t *testing.T, id bson.ObjectID) bson.Raw {
	t.Helper()
	raw, err := e.coll.FindOne(testContext(t), bson.D{{Key: "_id", Value: id}}).Raw()
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

type storedEvent struct {
	Type    string    `bson:"type"`
	StepKey *string   `bson:"step_key"`
	Actor   string    `bson:"actor"`
	At      time.Time `bson:"at"`
	Detail  struct {
		Connector     string     `bson:"connector"`
		Attempt       int        `bson:"attempt"`
		Fence         int64      `bson:"fence"`
		ExternalRef   string     `bson:"external_ref"`
		Error         string     `bson:"error"`
		NextAttemptAt *time.Time `bson:"next_attempt_at"`
	} `bson:"detail"`
}

type storedDoc struct {
	Provisioning []Job         `bson:"provisioning"`
	Events       []storedEvent `bson:"events"`
}

func (e *env) load(t *testing.T, id bson.ObjectID) storedDoc {
	t.Helper()
	var d storedDoc
	if err := bson.Unmarshal(e.raw(t, id), &d); err != nil {
		t.Fatal(err)
	}
	return d
}

func (e *env) job(t *testing.T, id bson.ObjectID, connector string) Job {
	t.Helper()
	for _, j := range e.load(t, id).Provisioning {
		if j.Connector == connector {
			return j
		}
	}
	t.Fatalf("job %s not found", connector)
	return Job{}
}

func (e *env) candidate(t *testing.T, id bson.ObjectID, connector string, now time.Time) Candidate {
	t.Helper()
	cands, err := e.store.Candidates(testContext(t), now, 100)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range cands {
		if c.InstanceID == id && c.Connector == connector {
			return c
		}
	}
	t.Fatalf("no candidate for %s at %v (got %+v)", connector, now, cands)
	return Candidate{}
}

func (e *env) assertNotCandidate(t *testing.T, id bson.ObjectID, connector string, now time.Time) {
	t.Helper()
	cands, err := e.store.Candidates(testContext(t), now, 100)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range cands {
		if c.InstanceID == id && c.Connector == connector {
			t.Fatalf("unexpected candidate %+v at %v", c, now)
		}
	}
}

func (e *env) claim(t *testing.T, c Candidate, worker string, now time.Time) Claim {
	t.Helper()
	claim, err := e.store.Claim(testContext(t), c, worker, now)
	if err != nil {
		t.Fatalf("claim %+v by %s at %v: %v", c, worker, now, err)
	}
	return claim
}

func assertClaimLost(t *testing.T, e *env, c Candidate, now time.Time) {
	t.Helper()
	claim, err := e.store.Claim(testContext(t), c, "intruder", now)
	if !errors.Is(err, ErrClaimLost) {
		t.Fatalf("claim %+v at %v = (%+v, %v), want ErrClaimLost", c, now, claim, err)
	}
}

func TestClaimConcurrentOneWinner(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	cand := e.candidate(t, id, google, t0)
	const n = 32
	var ready, done sync.WaitGroup
	start := make(chan struct{})
	var wins, lost atomic.Int32
	winners := make(chan string, n)
	errs := make(chan error, n)
	for i := range n {
		ready.Add(1)
		done.Add(1)
		go func() {
			defer done.Done()
			worker := "w" + string(rune('A'+i))
			ready.Done()
			<-start
			_, err := e.store.Claim(context.Background(), cand, worker, t0)
			switch {
			case err == nil:
				wins.Add(1)
				winners <- worker
			case errors.Is(err, ErrClaimLost):
				lost.Add(1)
			default:
				errs <- err
			}
		}()
	}
	ready.Wait()
	close(start)
	done.Wait()
	close(errs)
	close(winners)
	for err := range errs {
		t.Fatal(err)
	}
	if wins.Load() != 1 || lost.Load() != n-1 {
		t.Fatalf("wins = %d, lost = %d; want 1 and %d", wins.Load(), lost.Load(), n-1)
	}
	j := e.job(t, id, google)
	if j.Status != StatusRunning || j.Fence != 1 || j.Attempts != 1 || j.LeaseOwner == nil || *j.LeaseOwner != <-winners {
		t.Fatalf("job after race = %+v", j)
	}
}

func TestStaleWorkerFenceRejected(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	stale := e.claim(t, e.candidate(t, id, google, t0), "A", t0)
	// A's lease expires (e.g. A hung past its call timeout); B reclaims.
	t1 := t0.Add(e.policy.Lease + time.Second)
	cand := e.candidate(t, id, google, t1)
	if cand.SeenStatus != StatusRunning || cand.SeenFence != stale.Fence {
		t.Fatalf("reclaim candidate = %+v", cand)
	}
	fresh := e.claim(t, cand, "B", t1)
	if fresh.Fence != stale.Fence+1 {
		t.Fatalf("fresh fence = %d, want %d", fresh.Fence, stale.Fence+1)
	}
	before := e.raw(t, id)
	e.setNow(t1.Add(time.Second))
	if err := e.store.Succeed(testContext(t), stale, "stale-ref"); !errors.Is(err, ErrFenceRejected) {
		t.Fatalf("stale Succeed = %v, want ErrFenceRejected", err)
	}
	for _, retryable := range []bool{true, false} {
		if err := e.store.RecordFailure(testContext(t), stale, "stale failure", retryable, e.policy, t1.Add(time.Second)); !errors.Is(err, ErrFenceRejected) {
			t.Fatalf("stale RecordFailure(retryable=%v) = %v, want ErrFenceRejected", retryable, err)
		}
	}
	if after := e.raw(t, id); !bytes.Equal(before, after) {
		t.Fatalf("stale worker changed the document:\nbefore %s\nafter  %s", before, after)
	}
	if err := e.store.Succeed(testContext(t), fresh, "ref-B"); err != nil {
		t.Fatal(err)
	}
	j := e.job(t, id, google)
	if j.Status != StatusSucceeded || *j.ExternalRef != "ref-B" || j.Fence != fresh.Fence || *j.LeaseOwner != "B" {
		t.Fatalf("job after fresh succeed = %+v", j)
	}
}

func TestLiveLeaseNotReclaimable(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	c := e.claim(t, e.candidate(t, id, google, t0), "A", t0)
	running := Candidate{InstanceID: id, Connector: google, SeenStatus: StatusRunning, SeenFence: c.Fence}
	for _, at := range []time.Time{t0, t0.Add(e.policy.Lease - time.Second), t0.Add(e.policy.Lease)} {
		assertClaimLost(t, e, running, at)
		e.assertNotCandidate(t, id, google, at)
	}
	if j := e.job(t, id, google); j.Fence != 1 || *j.LeaseOwner != "A" {
		t.Fatalf("live lease job changed: %+v", j)
	}
}

func TestExpiredLeaseReclaimable(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	e.claim(t, e.candidate(t, id, google, t0), "A", t0)
	t1 := t0.Add(e.policy.Lease + time.Millisecond)
	c := e.claim(t, e.candidate(t, id, google, t1), "B", t1)
	if c.Fence != 2 || c.Attempt != 2 {
		t.Fatalf("reclaim = %+v, want fence 2 attempt 2", c)
	}
	j := e.job(t, id, google)
	if j.Status != StatusRunning || *j.LeaseOwner != "B" || !j.LeaseUntil.Equal(t1.Add(e.policy.Lease)) {
		t.Fatalf("reclaimed job = %+v", j)
	}
}

func TestRetryNotDueNotClaimable(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	c := e.claim(t, e.candidate(t, id, google, t0), "A", t0)
	failedAt := t0.Add(time.Second)
	if err := e.store.RecordFailure(testContext(t), c, "503", true, e.policy, failedAt); err != nil {
		t.Fatal(err)
	}
	j := e.job(t, id, google)
	dueAt := failedAt.Add(e.policy.Base) // maxRand: attempt 1 ceiling = Base
	if j.Status != StatusRetry || !j.NextAttemptAt.Equal(dueAt) {
		t.Fatalf("job after retryable failure = %+v, want retry at %v", j, dueAt)
	}
	retry := Candidate{InstanceID: id, Connector: google, SeenStatus: StatusRetry, SeenFence: c.Fence}
	early := dueAt.Add(-time.Millisecond)
	assertClaimLost(t, e, retry, early)
	e.assertNotCandidate(t, id, google, early)
	if c2 := e.claim(t, e.candidate(t, id, google, dueAt), "B", dueAt); c2.Fence != 2 || c2.Attempt != 2 {
		t.Fatalf("due retry claim = %+v", c2)
	}
}

func TestManualRetryKeepsFence(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	now := t0
	var last Claim
	for round := 1; round <= 3; round++ {
		last = e.claim(t, e.candidate(t, id, google, now), "A", now)
		if last.Attempt != 1 || last.Fence != int64(round) {
			t.Fatalf("round %d claim = %+v, want attempt 1 fence %d", round, last, round)
		}
		if err := e.store.RecordFailure(testContext(t), last, "400", false, e.policy, now); err != nil {
			t.Fatal(err)
		}
		now = now.Add(time.Second)
		// IT manual retry as Django performs it: pending, attempts 0, next_attempt_at now; fence untouched.
		e.setJob(t, id, google, bson.D{{Key: "status", Value: "pending"}, {Key: "attempts", Value: int32(0)}, {Key: "next_attempt_at", Value: now}})
		if j := e.job(t, id, google); j.Fence != last.Fence {
			t.Fatalf("manual retry changed fence: %d -> %d", last.Fence, j.Fence)
		}
	}
	next := e.claim(t, e.candidate(t, id, google, now), "A", now)
	if next.Fence != last.Fence+1 || next.Attempt != 1 {
		t.Fatalf("claim after manual retry = %+v, want fence %d attempt 1", next, last.Fence+1)
	}
}

func TestStaleCandidateAfterManualRetry(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	old := e.candidate(t, id, google, t0) // {pending, fence 0}, read by a slow worker
	c := e.claim(t, old, "A", t0)
	if err := e.store.RecordFailure(testContext(t), c, "400", false, e.policy, t0); err != nil {
		t.Fatal(err)
	}
	t1 := t0.Add(time.Second)
	e.setJob(t, id, google, bson.D{{Key: "status", Value: "pending"}, {Key: "attempts", Value: int32(0)}, {Key: "next_attempt_at", Value: t1}})
	// The job is pending and due again, but in a newer round: the old candidate must not claim it.
	assertClaimLost(t, e, old, t1.Add(time.Second))
	if j := e.job(t, id, google); j.Status != StatusPending || j.Fence != 1 {
		t.Fatalf("job after stale claim attempt = %+v", j)
	}
	if fresh := e.claim(t, e.candidate(t, id, google, t1), "B", t1); fresh.Fence != 2 {
		t.Fatalf("fresh claim fence = %d, want 2", fresh.Fence)
	}
}

func TestOverriddenNotClaimable(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	seen := e.candidate(t, id, google, t0)
	// IT overrides between the worker's read and its claim; fence and next_attempt_at unchanged.
	e.setJob(t, id, google, bson.D{{Key: "status", Value: "overridden"}, {Key: "override_reason", Value: "created by hand"}})
	assertClaimLost(t, e, seen, t0)
	e.assertNotCandidate(t, id, google, t0)
	if j := e.job(t, id, google); j.Status != StatusOverridden || j.Fence != 0 || j.Attempts != 0 {
		t.Fatalf("overridden job changed: %+v", j)
	}
}

func TestSucceededNotClaimable(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	c := e.claim(t, e.candidate(t, id, google, t0), "A", t0)
	if err := e.store.Succeed(testContext(t), c, "ref-1"); err != nil {
		t.Fatal(err)
	}
	later := t0.Add(time.Hour)
	for _, status := range []Status{StatusPending, StatusRetry} {
		assertClaimLost(t, e, Candidate{InstanceID: id, Connector: google, SeenStatus: status, SeenFence: c.Fence}, later)
	}
	assertClaimLost(t, e, Candidate{InstanceID: id, Connector: google, SeenStatus: StatusSucceeded, SeenFence: c.Fence}, later)
	e.assertNotCandidate(t, id, google, later)
	if j := e.job(t, id, google); j.Status != StatusSucceeded || j.Fence != 1 || *j.ExternalRef != "ref-1" {
		t.Fatalf("succeeded job changed: %+v", j)
	}
}

func TestMaxAttemptsDead(t *testing.T) {
	p := validPolicy()
	p.MaxAttempts = 3
	e := newEnv(t, p)
	id := e.seed(t)
	now := t0
	want := []Status{StatusRetry, StatusRetry, StatusDead}
	for i, status := range want {
		c := e.claim(t, e.candidate(t, id, google, now), "A", now)
		if c.Attempt != i+1 {
			t.Fatalf("attempt = %d, want %d", c.Attempt, i+1)
		}
		if err := e.store.RecordFailure(testContext(t), c, "503", true, p, now); err != nil {
			t.Fatal(err)
		}
		j := e.job(t, id, google)
		if j.Status != status || *j.LastError != "503" {
			t.Fatalf("after attempt %d: %+v, want %s", i+1, j, status)
		}
		if status == StatusRetry {
			if ceiling := backoff.Ceiling(c.Attempt, p.Base, p.Cap); !j.NextAttemptAt.Equal(now.Add(ceiling)) {
				t.Fatalf("next_attempt_at = %v, want %v", j.NextAttemptAt, now.Add(ceiling))
			}
			now = *j.NextAttemptAt
		}
	}
	e.assertNotCandidate(t, id, google, now.Add(24*time.Hour))
	events := e.load(t, id).Events
	var types []string
	for _, ev := range events[2:] {
		types = append(types, ev.Type)
	}
	if got := strings.Join(types, ","); got != "provisioning.retry,provisioning.retry,provisioning.dead" {
		t.Fatalf("event types = %s", got)
	}
}

func TestNonRetryableDeadImmediately(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	c := e.claim(t, e.candidate(t, id, google, t0), "A", t0)
	if err := e.store.RecordFailure(testContext(t), c, "422 invalid username", false, e.policy, t0); err != nil {
		t.Fatal(err)
	}
	j := e.job(t, id, google)
	if j.Status != StatusDead || j.Attempts != 1 || *j.LastError != "422 invalid username" {
		t.Fatalf("job = %+v, want dead after 1 attempt", j)
	}
	e.assertNotCandidate(t, id, google, t0.Add(24*time.Hour))
}

func TestEventsPushed(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	initial := len(e.load(t, id).Events)

	g := e.claim(t, e.candidate(t, id, google, t0), "w1", t0)
	if n := len(e.load(t, id).Events); n != initial {
		t.Fatalf("claim pushed %d events", n-initial)
	}
	retryAt := t0.Add(time.Second)
	if err := e.store.RecordFailure(testContext(t), g, "timeout", true, e.policy, retryAt); err != nil {
		t.Fatal(err)
	}
	ev := e.load(t, id).Events[initial]
	if ev.Type != EventRetry || ev.Actor != "provisioner:w1" || ev.StepKey != nil || !ev.At.Equal(retryAt) ||
		ev.Detail.Connector != google || ev.Detail.Attempt != 1 || ev.Detail.Fence != 1 || ev.Detail.Error != "timeout" ||
		ev.Detail.NextAttemptAt == nil || !ev.Detail.NextAttemptAt.Equal(retryAt.Add(e.policy.Base)) {
		t.Fatalf("retry event = %+v", ev)
	}

	g2 := e.claim(t, e.candidate(t, id, google, retryAt.Add(e.policy.Base)), "w2", retryAt.Add(e.policy.Base))
	succeededAt := t0.Add(time.Minute)
	e.setNow(succeededAt)
	if err := e.store.Succeed(testContext(t), g2, "ext-42"); err != nil {
		t.Fatal(err)
	}
	ev = e.load(t, id).Events[initial+1]
	if ev.Type != EventSucceeded || ev.Actor != "provisioner:w2" || !ev.At.Equal(succeededAt) ||
		ev.Detail.Connector != google || ev.Detail.Attempt != 2 || ev.Detail.Fence != 2 || ev.Detail.ExternalRef != "ext-42" {
		t.Fatalf("succeeded event = %+v", ev)
	}

	s := e.claim(t, e.candidate(t, id, slack, t0), "w3", t0)
	if err := e.store.RecordFailure(testContext(t), s, "403", false, e.policy, t0); err != nil {
		t.Fatal(err)
	}
	events := e.load(t, id).Events
	ev = events[initial+2]
	if len(events) != initial+3 || ev.Type != EventDead || ev.Actor != "provisioner:w3" ||
		ev.Detail.Connector != slack || ev.Detail.Attempt != 1 || ev.Detail.Fence != 1 || ev.Detail.Error != "403" {
		t.Fatalf("dead event = %+v (events %d)", ev, len(events))
	}
}

// assertOwnership checks that only the target job element and the events array changed,
// that version/steps/issues and every other field are byte-identical, and that events grew by newEvents.
func assertOwnership(t *testing.T, before, after bson.Raw, connector string, newEvents int) {
	t.Helper()
	be, err := before.Elements()
	if err != nil {
		t.Fatal(err)
	}
	ae, err := after.Elements()
	if err != nil {
		t.Fatal(err)
	}
	if len(be) != len(ae) {
		t.Fatalf("top-level field count %d -> %d", len(be), len(ae))
	}
	for _, key := range []string{"version", "steps", "issues"} {
		if _, err := before.LookupErr(key); err != nil {
			t.Fatalf("seed lacks %s", key)
		}
	}
	for i, b := range be {
		a := ae[i]
		if b.Key() != a.Key() {
			t.Fatalf("field order changed: %s -> %s", b.Key(), a.Key())
		}
		if b.Key() == "provisioning" || b.Key() == "events" {
			continue
		}
		if !bytes.Equal(b, a) {
			t.Fatalf("field %s changed: %s -> %s", b.Key(), b.Value(), a.Value())
		}
	}
	bj, _ := before.Lookup("provisioning").Array().Values()
	aj, _ := after.Lookup("provisioning").Array().Values()
	if len(bj) != len(aj) {
		t.Fatalf("provisioning length %d -> %d", len(bj), len(aj))
	}
	for i := range bj {
		if bj[i].Document().Lookup("connector").StringValue() == connector {
			if aj[i].Document().Lookup("connector").StringValue() != connector {
				t.Fatalf("job order changed")
			}
			continue
		}
		if !bytes.Equal(bj[i].Value, aj[i].Value) {
			t.Fatalf("other job element changed: %s -> %s", bj[i], aj[i])
		}
	}
	bev, _ := before.Lookup("events").Array().Values()
	aev, _ := after.Lookup("events").Array().Values()
	if len(aev) != len(bev)+newEvents {
		t.Fatalf("events %d -> %d, want +%d", len(bev), len(aev), newEvents)
	}
	for i := range bev {
		if !bytes.Equal(bev[i].Value, aev[i].Value) {
			t.Fatalf("existing event %d changed", i)
		}
	}
	for _, ev := range aev[len(bev):] {
		if typ := ev.Document().Lookup("type").StringValue(); !strings.HasPrefix(typ, "provisioning.") {
			t.Fatalf("pushed non-provisioning event %q", typ)
		}
	}
}

func TestOwnership(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	step := func(connector string, newEvents int, op func()) {
		t.Helper()
		before := e.raw(t, id)
		op()
		assertOwnership(t, before, e.raw(t, id), connector, newEvents)
	}
	var g, s Claim
	step(google, 0, func() { g = e.claim(t, e.candidate(t, id, google, t0), "A", t0) })
	step(google, 1, func() {
		if err := e.store.RecordFailure(testContext(t), g, "503", true, e.policy, t0); err != nil {
			t.Fatal(err)
		}
	})
	due := t0.Add(e.policy.Base)
	step(google, 0, func() { g = e.claim(t, e.candidate(t, id, google, due), "A", due) })
	step(google, 1, func() {
		if err := e.store.RecordFailure(testContext(t), g, "400", false, e.policy, due); err != nil {
			t.Fatal(err)
		}
	})
	step(slack, 0, func() { s = e.claim(t, e.candidate(t, id, slack, t0), "B", t0) })
	step(slack, 1, func() {
		if err := e.store.Succeed(testContext(t), s, "ref-s"); err != nil {
			t.Fatal(err)
		}
	})
	// A rejected finish changes nothing at all.
	before := e.raw(t, id)
	if err := e.store.Succeed(testContext(t), g, "late"); !errors.Is(err, ErrFenceRejected) {
		t.Fatalf("finish on dead job = %v", err)
	}
	if !bytes.Equal(before, e.raw(t, id)) {
		t.Fatal("rejected finish changed the document")
	}
	// Counters keep the BSON type Django wrote (int32), so a $jsonSchema on the shared document stays satisfied.
	for _, v := range mustValues(t, e.raw(t, id).Lookup("provisioning").Array()) {
		for _, key := range []string{"fence", "attempts"} {
			if typ := v.Document().Lookup(key).Type; typ != bson.TypeInt32 {
				t.Fatalf("%s type = %s, want int32", key, typ)
			}
		}
	}
}

func mustValues(t *testing.T, a bson.RawArray) []bson.RawValue {
	t.Helper()
	v, err := a.Values()
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func TestCandidatesFlattenAndLimit(t *testing.T) {
	e := newEnv(t, validPolicy())
	a, b, c := e.seed(t), e.seed(t), e.seed(t)
	e.setJob(t, b, slack, bson.D{{Key: "next_attempt_at", Value: t0.Add(time.Hour)}}) // not yet due
	e.setJob(t, c, slack, bson.D{{Key: "status", Value: "overridden"}})               // never due
	e.claim(t, e.candidate(t, c, google, t0), "A", t0)                                // running, live lease
	got, err := e.store.Candidates(testContext(t), t0, 100)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]bool{a.Hex() + google: true, a.Hex() + slack: true, b.Hex() + google: true}
	if len(got) != len(want) {
		t.Fatalf("candidates = %+v, want %d", got, len(want))
	}
	for _, cand := range got {
		if !want[cand.InstanceID.Hex()+cand.Connector] || cand.SeenStatus != StatusPending || cand.SeenFence != 0 {
			t.Fatalf("unexpected candidate %+v", cand)
		}
	}
	if got, err := e.store.Candidates(testContext(t), t0, 2); err != nil || len(got) != 2 {
		t.Fatalf("limit 2 = %d candidates, %v", len(got), err)
	}
	if got, err := e.store.Candidates(testContext(t), t0.Add(e.policy.Lease+time.Second), 100); err != nil || len(got) != 4 {
		t.Fatalf("after lease expiry = %+v, %v; want 4 (a/google, a/slack, b/google, expired c/google)", got, err)
	}
}

func TestCandidatesUsesIndex(t *testing.T) {
	e := newEnv(t, validPolicy())
	for range 3 {
		e.seed(t)
	}
	var res bson.Raw
	err := e.db.RunCommand(testContext(t), bson.D{
		{Key: "explain", Value: bson.D{{Key: "find", Value: collName}, {Key: "filter", Value: candidatesFilter(t0)}}},
		{Key: "verbosity", Value: "queryPlanner"},
	}).Decode(&res)
	if err != nil {
		t.Fatal(err)
	}
	plan := res.Lookup("queryPlanner", "winningPlan").String()
	if !strings.Contains(plan, "IXSCAN") || !strings.Contains(plan, indexName) || strings.Contains(plan, "COLLSCAN") {
		t.Fatalf("winning plan does not use %s: %s", indexName, plan)
	}
}

func TestClaimDuplicateConnectorGuards(t *testing.T) {
	e := newEnv(t, validPolicy())
	for name, change := range map[string]func(*Job){
		"status": func(j *Job) { j.Status = StatusRetry },
		"fence":  func(j *Job) { j.Fence++ },
		"due":    func(j *Job) { future := t0.Add(time.Hour); j.NextAttemptAt = &future },
	} {
		t.Run(name, func(t *testing.T) {
			seed := e.seed(t)
			first := e.job(t, seed, google)
			second := first
			change(&second)
			id := bson.NewObjectID()
			if _, err := e.coll.InsertOne(testContext(t), bson.D{
				{Key: "_id", Value: id}, {Key: "provisioning", Value: []Job{first, second}},
				{Key: "events", Value: bson.A{}},
			}); err != nil {
				t.Fatal(err)
			}
			before := e.load(t, id)
			claim := e.claim(t, Candidate{InstanceID: id, Connector: google,
				SeenStatus: first.Status, SeenFence: first.Fence}, "worker", t0)
			after := e.load(t, id)
			if !reflect.DeepEqual(after.Provisioning[1], before.Provisioning[1]) {
				t.Fatal("claim modified duplicate outside its status/fence/due guards")
			}
			if claim.Fence != first.Fence+1 || after.Provisioning[0].Status != StatusRunning {
				t.Fatalf("claim did not transition first element: %+v", after)
			}
		})
	}
}

func TestFinishDuplicateConnectorStatusGuard(t *testing.T) {
	e := newEnv(t, validPolicy())
	id := e.seed(t)
	claim := e.claim(t, e.candidate(t, id, google, t0), "worker", t0)
	second := e.job(t, id, google)
	second.Status = StatusPending // Same connector and fence; only status distinguishes it.
	if _, err := e.coll.UpdateOne(testContext(t), bson.D{{Key: "_id", Value: id}},
		bson.D{{Key: "$push", Value: bson.D{{Key: "provisioning", Value: second}}}}); err != nil {
		t.Fatal(err)
	}
	before := e.load(t, id)
	if err := e.store.Succeed(testContext(t), claim, "account"); err != nil {
		t.Fatal(err)
	}
	after := e.load(t, id)
	if !reflect.DeepEqual(after.Provisioning[2], before.Provisioning[2]) {
		t.Fatal("finish modified duplicate outside its status guard")
	}
}
