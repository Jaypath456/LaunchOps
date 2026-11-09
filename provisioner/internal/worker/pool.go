// Package worker runs provisioning jobs: poll due candidates, claim one with compare-and-set, call the
// connector, record the result under the claim's fence.
package worker

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math/rand/v2"
	"sync"
	"time"

	"launchops/provisioner/internal/backoff"
	"launchops/provisioner/internal/connectors"
	"launchops/provisioner/internal/jobs"
)

// Store is the subset of *jobs.Store the pool uses.
type Store interface {
	Candidates(ctx context.Context, now time.Time, limit int) ([]jobs.Candidate, error)
	Claim(ctx context.Context, cand jobs.Candidate, workerID string, now time.Time) (jobs.Claim, error)
	Succeed(ctx context.Context, claim jobs.Claim, externalRef string) error
	RecordFailure(ctx context.Context, claim jobs.Claim, cause string, retryable bool, policy jobs.Policy, now time.Time) error
}

// Connector creates the external account. Errors carry retryability (connectors.IsRetryable).
type Connector interface {
	CreateAccount(ctx context.Context, req connectors.Request) (string, error)
}

const (
	DefaultDrainTimeout  = 20 * time.Second
	DefaultBatch         = 8
	DefaultPollMin       = time.Second
	DefaultPollMax       = 5 * time.Second
	DefaultFinishTimeout = 5 * time.Second
)

// ErrDrainTimeout means in-flight calls were cancelled at shutdown; their jobs stay running until the lease expires.
var ErrDrainTimeout = errors.New("drain timeout: in-flight calls cancelled, their jobs are left for lease reclaim")

// Pool runs Workers goroutines. Zero values of the optional fields take the defaults above, except
// DrainTimeout, which is used as given (0 = cancel in-flight calls as soon as shutdown starts).
type Pool struct {
	Store     Store
	Connector Connector
	Policy    jobs.Policy
	// WorkerID identifies the replica; goroutine i uses WorkerID/i as lease owner and in logs.
	WorkerID string
	Workers  int
	Logger   *slog.Logger

	DrainTimeout  time.Duration
	Batch         int
	PollMin       time.Duration
	PollMax       time.Duration
	FinishTimeout time.Duration
	// Now is the worker clock for candidates, claims and backoff (default time.Now).
	Now func() time.Time
}

func (p Pool) withDefaults() (Pool, error) {
	switch {
	case p.Store == nil:
		return p, errors.New("worker pool: store is required")
	case p.Connector == nil:
		return p, errors.New("worker pool: connector is required")
	case p.WorkerID == "":
		return p, errors.New("worker pool: worker id is required")
	case p.Workers < 1:
		return p, fmt.Errorf("worker pool: workers must be at least 1, got %d", p.Workers)
	case p.DrainTimeout < 0:
		return p, fmt.Errorf("worker pool: drain timeout must not be negative, got %v", p.DrainTimeout)
	case p.Batch < 0 || p.PollMin < 0 || p.PollMax < 0 || p.FinishTimeout < 0:
		return p, errors.New("worker pool: batch, poll and finish settings must not be negative")
	}
	if p.Logger == nil {
		p.Logger = slog.New(slog.NewJSONHandler(io.Discard, nil))
	}
	if p.Batch == 0 {
		p.Batch = DefaultBatch
	}
	if p.PollMin == 0 {
		p.PollMin = DefaultPollMin
	}
	if p.PollMax == 0 {
		p.PollMax = DefaultPollMax
	}
	if p.PollMax < p.PollMin {
		return p, fmt.Errorf("worker pool: poll max %v is below poll min %v", p.PollMax, p.PollMin)
	}
	if p.FinishTimeout == 0 {
		p.FinishTimeout = DefaultFinishTimeout
	}
	p.Policy.FinishTimeout = p.FinishTimeout
	if err := p.Policy.Validate(); err != nil {
		return p, fmt.Errorf("worker pool: %w", err)
	}
	if p.Now == nil {
		p.Now = time.Now
	}
	return p, nil
}

// Run processes jobs until ctx is cancelled, then stops claiming and waits up to DrainTimeout for
// in-flight jobs. At that absolute shutdown cutoff it cancels all calls, claims and finishes, then
// returns ErrDrainTimeout. A clean drain returns nil.
func (p *Pool) Run(ctx context.Context) error {
	cfg, err := p.withDefaults()
	if err != nil {
		return err
	}
	// All in-flight operations share the drain cutoff. They may complete during the drain,
	// but no claim or finish gets a fresh budget after it expires.
	callCtx, abortCalls := context.WithCancel(context.Background())
	defer abortCalls()

	var wg sync.WaitGroup
	for i := range cfg.Workers {
		w := &worker{cfg: &cfg, id: fmt.Sprintf("%s/%d", cfg.WorkerID, i), runCtx: ctx, callCtx: callCtx}
		wg.Go(w.loop)
	}
	done := make(chan struct{})
	go func() {
		wg.Wait()
		close(done)
	}()

	select {
	case <-done:
		return nil
	case <-ctx.Done():
	}
	cfg.Logger.Info("shutdown: stopped claiming, draining in-flight jobs", "worker_id", cfg.WorkerID, "drain_timeout", cfg.DrainTimeout.String())
	timer := time.NewTimer(cfg.DrainTimeout)
	defer timer.Stop()
	select {
	case <-done:
		cfg.Logger.Info("shutdown: drained", "worker_id", cfg.WorkerID)
		return nil
	case <-timer.C:
	}
	cfg.Logger.Warn("shutdown: drain timeout, cancelling in-flight operations", "worker_id", cfg.WorkerID)
	abortCalls()
	<-done
	return ErrDrainTimeout
}

type worker struct {
	cfg     *Pool
	id      string
	runCtx  context.Context
	callCtx context.Context
}

func (w *worker) loop() {
	idle := 0
	for w.runCtx.Err() == nil {
		claim, ok := w.claimNext()
		if !ok {
			if !w.sleep(idle) {
				return
			}
			idle++
			continue
		}
		idle = 0
		w.process(claim)
		if w.callCtx.Err() != nil {
			return
		}
	}
}

// claimNext reads a batch of due candidates and tries them in random order until one claim wins.
func (w *worker) claimNext() (jobs.Claim, bool) {
	cands, err := w.cfg.Store.Candidates(w.runCtx, w.cfg.Now(), w.cfg.Batch)
	if err != nil {
		if w.runCtx.Err() == nil {
			w.cfg.Logger.Error("candidates query failed", "worker_id", w.id, "error", err.Error())
		}
		return jobs.Claim{}, false
	}
	rand.Shuffle(len(cands), func(i, j int) { cands[i], cands[j] = cands[j], cands[i] })
	for _, cand := range cands {
		// No claim starts after shutdown begins.
		if w.runCtx.Err() != nil {
			return jobs.Claim{}, false
		}
		// Claims may finish during the drain, but share its absolute cutoff.
		claimAt := w.cfg.Now()
		ctx, cancel := context.WithTimeout(w.callCtx, w.cfg.Policy.StoreOpTimeout)
		claim, err := w.cfg.Store.Claim(ctx, cand, w.id, claimAt)
		cancel()
		if errors.Is(err, jobs.ErrClaimLost) {
			continue
		}
		if err != nil {
			w.cfg.Logger.Error("claim failed", "worker_id", w.id, "connector", cand.Connector, "error", err.Error())
			return jobs.Claim{}, false
		}
		if claimAt.Add(w.cfg.Policy.Lease).Sub(w.cfg.Now()) < w.cfg.Policy.CallTimeout+w.cfg.FinishTimeout {
			w.cfg.Logger.Warn("job attempt", "worker_id", w.id, "job_key", claim.IdempotencyKey,
				"connector", claim.Connector, "fence", claim.Fence, "attempt", claim.Attempt,
				"outcome", "abandoned_late_claim")
			return jobs.Claim{}, false
		}
		if w.callCtx.Err() != nil {
			return jobs.Claim{}, false
		}
		return claim, true
	}
	return jobs.Claim{}, false
}

// sleep waits about PollMin, growing to PollMax while idle, with jitter. False means shutdown.
func (w *worker) sleep(idle int) bool {
	base := backoff.Ceiling(idle+1, w.cfg.PollMin, w.cfg.PollMax)
	d := base*4/5 + time.Duration(rand.Int64N(int64(base)*2/5+1))
	d = min(d, w.cfg.PollMax)
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-timer.C:
		return true
	case <-w.runCtx.Done():
		return false
	}
}

func (w *worker) process(claim jobs.Claim) {
	start := time.Now()
	ctx, cancel := context.WithTimeout(w.callCtx, w.cfg.Policy.CallTimeout)
	ref, callErr := w.cfg.Connector.CreateAccount(ctx, connectors.Request{
		Connector: claim.Connector, IdempotencyKey: claim.IdempotencyKey, Payload: claim.Payload,
	})
	cancel()
	attrs := []any{"worker_id", w.id, "job_key", claim.IdempotencyKey, "connector", claim.Connector,
		"fence", claim.Fence, "attempt", claim.Attempt}

	if w.callCtx.Err() != nil {
		// Aborted after the drain timeout: write nothing; the job stays running and is reclaimed after the lease.
		w.cfg.Logger.Warn("job attempt", append(attrs, "outcome", "abandoned", "error", w.callCtx.Err().Error(),
			"duration_ms", time.Since(start).Milliseconds())...)
		return
	}

	// A finish can outlive the shutdown signal, but never the shared drain cutoff.
	fctx, fcancel := context.WithTimeout(w.callCtx, w.cfg.FinishTimeout)
	defer fcancel()
	var outcome string
	var finishErr error
	if callErr == nil {
		outcome = string(jobs.StatusSucceeded)
		finishErr = w.cfg.Store.Succeed(fctx, claim, ref)
		attrs = append(attrs, "external_ref", ref)
	} else {
		retryable := connectors.IsRetryable(callErr)
		outcome = string(jobs.NextState(claim.Attempt, retryable, w.cfg.Policy.MaxAttempts))
		finishErr = w.cfg.Store.RecordFailure(fctx, claim, callErr.Error(), retryable, w.cfg.Policy, w.cfg.Now())
		attrs = append(attrs, "error", callErr.Error(), "retryable", retryable)
	}
	level := slog.LevelInfo
	switch {
	case errors.Is(finishErr, jobs.ErrFenceRejected):
		// A newer claim owns the job; this attempt's result is discarded. Not an error exit.
		outcome, level = "fence_rejected", slog.LevelWarn
	case finishErr != nil:
		// The job stays running and is reclaimed after the lease; the retry replays under the same key.
		outcome, level = "finish_failed", slog.LevelError
		attrs = append(attrs, "finish_error", finishErr.Error())
	}
	w.cfg.Logger.Log(context.Background(), level, "job attempt",
		append(attrs, "outcome", outcome, "duration_ms", time.Since(start).Milliseconds())...)
}
