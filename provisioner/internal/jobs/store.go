package jobs

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"time"

	"launchops/provisioner/internal/backoff"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
	"go.mongodb.org/mongo-driver/v2/mongo/writeconcern"
)

// elem is the arrayFilters identifier; every write targets provisioning.$[j] where j.connector matches.
// arrayFilters only select the element to write; the $elemMatch in each query filter is the guard.
const elem = "provisioning.$[j]."

type StoreConfig struct {
	// Policy is validated by NewStore; Claim uses Policy.Lease.
	Policy Policy
	// OpTimeout bounds every MongoDB call.
	OpTimeout time.Duration
	// Now stamps Succeed (default time.Now). Claim and RecordFailure take an explicit now.
	Now func() time.Time
	// Rand drives backoff jitter (default backoff.SystemRand).
	Rand   backoff.Rand
	Logger *slog.Logger
}

type Store struct {
	coll *mongo.Collection
	cfg  StoreConfig
}

func NewStore(client *mongo.Client, database, collection string, cfg StoreConfig) (*Store, error) {
	if client == nil {
		return nil, errors.New("MongoDB client is required")
	}
	if database == "" || collection == "" {
		return nil, errors.New("database and collection names are required")
	}
	if err := cfg.Policy.Validate(); err != nil {
		return nil, err
	}
	if cfg.OpTimeout <= 0 {
		return nil, fmt.Errorf("operation timeout must be positive, got %v", cfg.OpTimeout)
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.Rand == nil {
		cfg.Rand = backoff.SystemRand{}
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.New(slog.NewJSONHandler(io.Discard, nil))
	}
	coll := client.Database(database).Collection(collection,
		options.Collection().SetWriteConcern(writeconcern.Majority()))
	return &Store{coll: coll, cfg: cfg}, nil
}

// candidatesFilter is a top-level $or of two $elemMatch clauses so each branch can use the
// multikey index {"provisioning.status": 1, "provisioning.next_attempt_at": 1}.
func candidatesFilter(now time.Time) bson.D {
	return bson.D{{Key: "$or", Value: bson.A{
		bson.D{{Key: "provisioning", Value: bson.D{{Key: "$elemMatch", Value: bson.D{
			{Key: "status", Value: bson.D{{Key: "$in", Value: bson.A{StatusPending, StatusRetry}}}},
			{Key: "next_attempt_at", Value: bson.D{{Key: "$lte", Value: now}}},
		}}}}},
		bson.D{{Key: "provisioning", Value: bson.D{{Key: "$elemMatch", Value: bson.D{
			{Key: "status", Value: StatusRunning},
			{Key: "lease_until", Value: bson.D{{Key: "$lt", Value: now}}},
		}}}}},
	}}}
}

// Candidates returns up to limit due jobs: pending/retry with next_attempt_at <= now, or running
// with lease_until < now. No sort: callers pick at random.
func (s *Store) Candidates(ctx context.Context, now time.Time, limit int) ([]Candidate, error) {
	if limit <= 0 {
		return nil, nil
	}
	now = now.UTC()
	ctx, cancel := context.WithTimeout(ctx, s.cfg.OpTimeout)
	defer cancel()
	cursor, err := s.coll.Find(ctx, candidatesFilter(now), options.Find().
		SetProjection(bson.D{{Key: "_id", Value: 1}, {Key: "provisioning", Value: 1}}).
		SetLimit(int64(limit)))
	if err != nil {
		return nil, fmt.Errorf("find candidates: %w", err)
	}
	var docs []struct {
		ID           bson.ObjectID `bson:"_id"`
		Provisioning []Job         `bson:"provisioning"`
	}
	if err := cursor.All(ctx, &docs); err != nil {
		return nil, fmt.Errorf("decode candidates: %w", err)
	}
	out := make([]Candidate, 0, limit)
	for _, doc := range docs {
		for _, job := range doc.Provisioning {
			if len(out) == limit {
				return out, nil
			}
			if isDue(job, now) {
				out = append(out, Candidate{InstanceID: doc.ID, Connector: job.Connector, SeenStatus: job.Status, SeenFence: job.Fence})
			}
		}
	}
	return out, nil
}

// dueCondition is the per-status due clause for Claim; ok is false for statuses that are never claimable.
func dueCondition(status Status, now time.Time) (bson.E, bool) {
	switch status {
	case StatusPending, StatusRetry:
		return bson.E{Key: "next_attempt_at", Value: bson.D{{Key: "$lte", Value: now}}}, true
	case StatusRunning:
		return bson.E{Key: "lease_until", Value: bson.D{{Key: "$lt", Value: now}}}, true
	}
	return bson.E{}, false
}

// claimFilter guards the claim: the job must still have the status and fence the candidate saw and still be due.
func claimFilter(c Candidate, now time.Time) (bson.D, bool) {
	due, ok := dueCondition(c.SeenStatus, now)
	if !ok {
		return nil, false
	}
	match := bson.D{
		{Key: "connector", Value: c.Connector},
		{Key: "status", Value: c.SeenStatus},
		{Key: "fence", Value: c.SeenFence},
	}
	match = append(match, due)
	return bson.D{{Key: "_id", Value: c.InstanceID}, {Key: "provisioning", Value: bson.D{{Key: "$elemMatch", Value: match}}}}, true
}

// finishFilter guards every finish: the job must still be running under this claim's fence.
func finishFilter(c Claim) bson.D {
	match := bson.D{
		{Key: "connector", Value: c.Connector},
		{Key: "status", Value: StatusRunning},
		{Key: "fence", Value: c.Fence},
	}
	return bson.D{{Key: "_id", Value: c.InstanceID}, {Key: "provisioning", Value: bson.D{{Key: "$elemMatch", Value: match}}}}
}

func arrayFilters(connector string) []any {
	return []any{bson.D{{Key: "j.connector", Value: connector}}}
}

// Claim takes the job with compare-and-set: running, fence+1, attempts+1, a lease for workerID.
func (s *Store) Claim(ctx context.Context, cand Candidate, workerID string, now time.Time) (Claim, error) {
	if workerID == "" {
		return Claim{}, errors.New("worker id is required")
	}
	now = now.UTC()
	filter, ok := claimFilter(cand, now)
	if !ok {
		return Claim{}, ErrClaimLost
	}
	update := bson.D{
		{Key: "$set", Value: bson.D{
			{Key: elem + "status", Value: StatusRunning},
			{Key: elem + "lease_owner", Value: workerID},
			{Key: elem + "lease_until", Value: now.Add(s.cfg.Policy.Lease)},
			{Key: elem + "updated_at", Value: now},
		}},
		// int32 keeps the BSON type of counters written by PyMongo (int32 + int32 stays int32).
		{Key: "$inc", Value: bson.D{{Key: elem + "fence", Value: int32(1)}, {Key: elem + "attempts", Value: int32(1)}}},
	}
	ctx, cancel := context.WithTimeout(ctx, s.cfg.OpTimeout)
	defer cancel()
	var doc struct {
		Provisioning []Job `bson:"provisioning"`
	}
	err := s.coll.FindOneAndUpdate(ctx, filter, update, options.FindOneAndUpdate().
		SetArrayFilters(arrayFilters(cand.Connector)).
		SetReturnDocument(options.After).
		SetProjection(bson.D{{Key: "provisioning", Value: bson.D{{Key: "$elemMatch", Value: bson.D{{Key: "connector", Value: cand.Connector}}}}}}),
	).Decode(&doc)
	if errors.Is(err, mongo.ErrNoDocuments) {
		return Claim{}, ErrClaimLost
	}
	if err != nil {
		return Claim{}, fmt.Errorf("claim %s:%s: %w", cand.InstanceID.Hex(), cand.Connector, err)
	}
	if len(doc.Provisioning) != 1 {
		return Claim{}, fmt.Errorf("claim %s:%s: updated element not returned", cand.InstanceID.Hex(), cand.Connector)
	}
	job := doc.Provisioning[0]
	claim := Claim{
		InstanceID: cand.InstanceID, Connector: job.Connector, IdempotencyKey: job.IdempotencyKey,
		Payload: job.Payload, Attempt: job.Attempts, Fence: job.Fence, WorkerID: workerID,
	}
	s.log(claim, "claimed")
	return claim, nil
}

// Succeed records the external account reference. ErrFenceRejected if the claim is stale.
func (s *Store) Succeed(ctx context.Context, claim Claim, externalRef string) error {
	now := s.cfg.Now().UTC()
	set := bson.D{
		{Key: elem + "status", Value: StatusSucceeded},
		{Key: elem + "external_ref", Value: externalRef},
		{Key: elem + "updated_at", Value: now},
	}
	detail := append(baseDetail(claim), bson.E{Key: "external_ref", Value: externalRef})
	return s.finish(ctx, claim, set, event(EventSucceeded, claim, now, detail), "succeeded")
}

// RecordFailure moves the job to retry (with backoff) or dead. ErrFenceRejected if the claim is stale.
func (s *Store) RecordFailure(ctx context.Context, claim Claim, cause string, retryable bool, policy Policy, now time.Time) error {
	now = now.UTC()
	next := NextState(claim.Attempt, retryable, policy.MaxAttempts)
	set := bson.D{
		{Key: elem + "status", Value: next},
		{Key: elem + "last_error", Value: cause},
		{Key: elem + "updated_at", Value: now},
	}
	detail := append(baseDetail(claim), bson.E{Key: "error", Value: cause})
	eventType := EventDead
	if next == StatusRetry {
		at := now.Add(backoff.FullJitter(claim.Attempt, policy.Base, policy.Cap, s.cfg.Rand))
		set = append(set, bson.E{Key: elem + "next_attempt_at", Value: at})
		detail = append(detail, bson.E{Key: "next_attempt_at", Value: at})
		eventType = EventRetry
	}
	return s.finish(ctx, claim, set, event(eventType, claim, now, detail), string(next))
}

func (s *Store) finish(ctx context.Context, claim Claim, set bson.D, ev bson.D, outcome string) error {
	ctx, cancel := context.WithTimeout(ctx, s.cfg.OpTimeout)
	defer cancel()
	res, err := s.coll.UpdateOne(ctx, finishFilter(claim),
		bson.D{{Key: "$set", Value: set}, {Key: "$push", Value: bson.D{{Key: "events", Value: ev}}}},
		options.UpdateOne().SetArrayFilters(arrayFilters(claim.Connector)))
	if err != nil {
		return fmt.Errorf("finish %s:%s: %w", claim.InstanceID.Hex(), claim.Connector, err)
	}
	if res.MatchedCount == 0 {
		s.log(claim, "fence_rejected")
		return ErrFenceRejected
	}
	s.log(claim, outcome)
	return nil
}

func baseDetail(c Claim) bson.D {
	return bson.D{{Key: "connector", Value: c.Connector}, {Key: "attempt", Value: c.Attempt}, {Key: "fence", Value: c.Fence}}
}

func event(eventType string, c Claim, at time.Time, detail bson.D) bson.D {
	return bson.D{
		{Key: "type", Value: eventType},
		{Key: "step_key", Value: nil},
		{Key: "actor", Value: "provisioner:" + c.WorkerID},
		{Key: "at", Value: at},
		{Key: "detail", Value: detail},
	}
}

func (s *Store) log(c Claim, outcome string) {
	level := slog.LevelInfo
	if outcome == "fence_rejected" {
		level = slog.LevelWarn
	}
	s.cfg.Logger.Log(context.Background(), level, "provisioning job",
		"worker_id", c.WorkerID, "job_key", c.IdempotencyKey, "fence", c.Fence, "attempt", c.Attempt, "outcome", outcome)
}
