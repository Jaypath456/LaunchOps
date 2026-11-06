// Package jobs is the provisioner's job store: provisioning elements inside workflow_instances
// documents are the durable queue. Every transition is a compare-and-set on one element.
package jobs

import (
	"errors"
	"fmt"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

type Status string

const (
	StatusPending    Status = "pending"
	StatusRunning    Status = "running"
	StatusRetry      Status = "retry"
	StatusSucceeded  Status = "succeeded"
	StatusDead       Status = "dead"
	StatusOverridden Status = "overridden"
)

// Event types the provisioner pushes. It never pushes any other type.
const (
	EventSucceeded = "provisioning.succeeded"
	EventRetry     = "provisioning.retry"
	EventDead      = "provisioning.dead"
)

var (
	// ErrClaimLost means the job was no longer in the state the candidate saw (another worker
	// claimed it, IT changed it, or it is not due).
	ErrClaimLost = errors.New("claim lost")
	// ErrFenceRejected means the job is no longer running under this claim's fence.
	ErrFenceRejected = errors.New("fence rejected")
)

type Payload struct {
	Username    string `bson:"username" json:"username"`
	DisplayName string `bson:"display_name" json:"display_name"`
	Department  string `bson:"department" json:"department"`
}

// Job is one element of workflow_instances.provisioning (spec section 4).
type Job struct {
	Connector      string     `bson:"connector" json:"connector"`
	IdempotencyKey string     `bson:"idempotency_key" json:"idempotency_key"`
	Payload        Payload    `bson:"payload" json:"payload"`
	Status         Status     `bson:"status" json:"status"`
	Attempts       int        `bson:"attempts" json:"attempts"`
	Fence          int64      `bson:"fence" json:"fence"`
	LeaseOwner     *string    `bson:"lease_owner" json:"lease_owner"`
	LeaseUntil     *time.Time `bson:"lease_until" json:"lease_until"`
	NextAttemptAt  *time.Time `bson:"next_attempt_at" json:"next_attempt_at"`
	LastError      *string    `bson:"last_error" json:"last_error"`
	ExternalRef    *string    `bson:"external_ref" json:"external_ref"`
	OverrideReason *string    `bson:"override_reason" json:"override_reason"`
	UpdatedAt      time.Time  `bson:"updated_at" json:"updated_at"`
}

// Candidate is a job that looked claimable when read. Claim re-checks everything it saw.
type Candidate struct {
	InstanceID bson.ObjectID
	Connector  string
	SeenStatus Status
	SeenFence  int64
}

// Claim is the right to run one attempt. Fence identifies the attempt in every finish.
type Claim struct {
	InstanceID     bson.ObjectID
	Connector      string
	IdempotencyKey string
	Payload        Payload
	Attempt        int
	Fence          int64
	WorkerID       string
}

// LeaseMargin is the minimum slack between the call timeout and the lease, covering the finish write.
const LeaseMargin = 5 * time.Second

type Policy struct {
	MaxAttempts int
	Base        time.Duration
	Cap         time.Duration
	Lease       time.Duration
	CallTimeout time.Duration
}

// Validate enforces Lease > CallTimeout + LeaseMargin, so no lease renewal is needed.
func (p Policy) Validate() error {
	switch {
	case p.MaxAttempts < 1:
		return fmt.Errorf("max attempts must be at least 1, got %d", p.MaxAttempts)
	case p.Base <= 0:
		return fmt.Errorf("backoff base must be positive, got %v", p.Base)
	case p.Cap < p.Base:
		return fmt.Errorf("backoff cap %v must be at least base %v", p.Cap, p.Base)
	case p.CallTimeout <= 0:
		return fmt.Errorf("call timeout must be positive, got %v", p.CallTimeout)
	case p.Lease <= p.CallTimeout+LeaseMargin:
		return fmt.Errorf("lease %v must exceed call timeout %v + margin %v", p.Lease, p.CallTimeout, LeaseMargin)
	}
	return nil
}

// NextState decides the status after a failed attempt. attempts counts the attempt that just failed.
func NextState(attempts int, retryable bool, maxAttempts int) Status {
	if !retryable || attempts >= maxAttempts {
		return StatusDead
	}
	return StatusRetry
}

// isDue mirrors the due condition used in Candidates and Claim.
func isDue(j Job, now time.Time) bool {
	switch j.Status {
	case StatusPending, StatusRetry:
		return j.NextAttemptAt != nil && !j.NextAttemptAt.After(now)
	case StatusRunning:
		return j.LeaseUntil != nil && j.LeaseUntil.Before(now)
	}
	return false
}
