// Package jobs defines the provisioning element shared with Django and MongoDB.
package jobs

import "time"

type Payload struct {
	Username    string `json:"username" bson:"username"`
	DisplayName string `json:"display_name" bson:"display_name"`
	Department  string `json:"department" bson:"department"`
}

// Job is one workflow_instances.provisioning element. Nullable fields remain
// present in JSON so a round trip preserves the complete contract.
type Job struct {
	Connector      string     `json:"connector" bson:"connector"`
	IdempotencyKey string     `json:"idempotency_key" bson:"idempotency_key"`
	Payload        Payload    `json:"payload" bson:"payload"`
	Status         string     `json:"status" bson:"status"`
	Attempts       int64      `json:"attempts" bson:"attempts"`
	Fence          int64      `json:"fence" bson:"fence"`
	LeaseOwner     *string    `json:"lease_owner" bson:"lease_owner"`
	LeaseUntil     *time.Time `json:"lease_until" bson:"lease_until"`
	NextAttemptAt  *time.Time `json:"next_attempt_at" bson:"next_attempt_at"`
	LastError      *string    `json:"last_error" bson:"last_error"`
	ExternalRef    *string    `json:"external_ref" bson:"external_ref"`
	OverrideReason *string    `json:"override_reason" bson:"override_reason"`
	UpdatedAt      time.Time  `json:"updated_at" bson:"updated_at"`
}
