package jobs

import (
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

func validPolicy() Policy {
	return Policy{MaxAttempts: 5, Base: time.Second, Cap: time.Minute, Lease: 30 * time.Second, CallTimeout: 5 * time.Second, StoreOpTimeout: 5 * time.Second, FinishTimeout: 5 * time.Second}
}

func TestPolicyValidate(t *testing.T) {
	if err := validPolicy().Validate(); err != nil {
		t.Fatalf("valid policy rejected: %v", err)
	}
	for name, mutate := range map[string]func(*Policy){
		"zero max attempts":        func(p *Policy) { p.MaxAttempts = 0 },
		"zero base":                func(p *Policy) { p.Base = 0 },
		"cap below base":           func(p *Policy) { p.Cap = p.Base / 2 },
		"zero store timeout":       func(p *Policy) { p.StoreOpTimeout = 0 },
		"negative store timeout":   func(p *Policy) { p.StoreOpTimeout = -time.Second },
		"zero finish timeout":      func(p *Policy) { p.FinishTimeout = 0 },
		"negative finish timeout":  func(p *Policy) { p.FinishTimeout = -time.Second },
		"lease equals full budget": func(p *Policy) { p.Lease = p.StoreOpTimeout + p.CallTimeout + p.FinishTimeout + LeaseMargin },
		"lease below full budget": func(p *Policy) {
			p.Lease = p.StoreOpTimeout + p.CallTimeout + p.FinishTimeout + LeaseMargin - time.Nanosecond
		},
		"zero call timeout":         func(p *Policy) { p.CallTimeout = 0 },
		"lease equals call timeout": func(p *Policy) { p.Lease = p.CallTimeout },
		"lease equals call+margin":  func(p *Policy) { p.Lease = p.CallTimeout + LeaseMargin },
		"lease below call timeout":  func(p *Policy) { p.Lease = p.CallTimeout - time.Second },
	} {
		p := validPolicy()
		mutate(&p)
		if err := p.Validate(); err == nil {
			t.Errorf("%s: Validate() = nil, want error", name)
		}
	}
	p := validPolicy()
	p.Lease = p.StoreOpTimeout + p.CallTimeout + p.FinishTimeout + LeaseMargin + time.Nanosecond
	if err := p.Validate(); err != nil {
		t.Fatalf("lease just above full budget rejected: %v", err)
	}
}

func TestNextState(t *testing.T) {
	for _, tc := range []struct {
		attempts, max int
		retryable     bool
		want          Status
	}{
		{1, 5, true, StatusRetry},
		{4, 5, true, StatusRetry},
		{5, 5, true, StatusDead},
		{6, 5, true, StatusDead},
		{1, 5, false, StatusDead},
		{1, 1, true, StatusDead},
	} {
		if got := NextState(tc.attempts, tc.retryable, tc.max); got != tc.want {
			t.Errorf("NextState(%d, %v, %d) = %s, want %s", tc.attempts, tc.retryable, tc.max, got, tc.want)
		}
	}
}

func TestNewStoreRejectsInvalidConfig(t *testing.T) {
	// mongo.Connect is lazy; no server is contacted here.
	client, err := mongo.Connect(options.Client().ApplyURI("mongodb://127.0.0.1:1"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Disconnect(t.Context()) })
	bad := validPolicy()
	bad.Lease = bad.CallTimeout
	for name, tc := range map[string]struct {
		db, coll string
		cfg      StoreConfig
	}{
		"invalid policy":   {"d", "c", StoreConfig{Policy: bad, OpTimeout: time.Second}},
		"zero timeout":     {"d", "c", StoreConfig{Policy: validPolicy()}},
		"empty database":   {"", "c", StoreConfig{Policy: validPolicy(), OpTimeout: time.Second}},
		"empty collection": {"d", "", StoreConfig{Policy: validPolicy(), OpTimeout: time.Second}},
	} {
		if _, err := NewStore(client, tc.db, tc.coll, tc.cfg); err == nil {
			t.Errorf("%s: NewStore succeeded, want error", name)
		}
	}
	if _, err := NewStore(nil, "d", "c", StoreConfig{Policy: validPolicy(), OpTimeout: time.Second}); err == nil {
		t.Error("nil client accepted")
	}
}
