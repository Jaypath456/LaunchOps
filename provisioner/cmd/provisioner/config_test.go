package main

import (
	"errors"
	"strings"
	"testing"
	"time"

	"launchops/provisioner/internal/jobs"
)

func env(vars map[string]string) func(string) (string, bool) {
	return func(name string) (string, bool) {
		v, ok := vars[name]
		return v, ok
	}
}

func host() (string, error) { return "prov-1", nil }

func TestConfigDefaults(t *testing.T) {
	c, err := loadConfig(env(nil), host)
	if err != nil {
		t.Fatal(err)
	}
	want := Config{
		MongoURI: "mongodb://localhost:27017", MongoDB: "launchops", Workers: 4, WorkerID: "prov-1",
		Policy:       jobs.Policy{MaxAttempts: 5, Base: time.Second, Cap: 60 * time.Second, Lease: 30 * time.Second, CallTimeout: 5 * time.Second, StoreOpTimeout: 5 * time.Second, FinishTimeout: 5 * time.Second},
		DrainTimeout: 20 * time.Second, MockSaaSURL: "http://localhost:8080", HealthAddr: ":8081",
	}
	if c != want {
		t.Fatalf("defaults = %+v\nwant       %+v", c, want)
	}
}

func TestConfigOverrides(t *testing.T) {
	c, err := loadConfig(env(map[string]string{
		"MONGO_URI": "mongodb://mongo:27017", "MONGO_DB": "other", "WORKERS": "8", "WORKER_ID": "w-9",
		"LEASE": "45s", "CALL_TIMEOUT": "10s", "MAX_ATTEMPTS": "3", "BACKOFF_BASE": "2s", "BACKOFF_CAP": "30s",
		"DRAIN_TIMEOUT": "0s", "MOCKSAAS_URL": "http://mock-saas:8080", "HEALTH_ADDR": ":9000",
	}), func() (string, error) { return "", errors.New("must not be called") })
	if err != nil {
		t.Fatal(err)
	}
	want := Config{
		MongoURI: "mongodb://mongo:27017", MongoDB: "other", Workers: 8, WorkerID: "w-9",
		Policy:       jobs.Policy{MaxAttempts: 3, Base: 2 * time.Second, Cap: 30 * time.Second, Lease: 45 * time.Second, CallTimeout: 10 * time.Second, StoreOpTimeout: 5 * time.Second, FinishTimeout: 5 * time.Second},
		DrainTimeout: 0, MockSaaSURL: "http://mock-saas:8080", HealthAddr: ":9000",
	}
	if c != want {
		t.Fatalf("config = %+v\nwant     %+v", c, want)
	}
}

func TestConfigValidation(t *testing.T) {
	for _, tc := range []struct {
		name string
		vars map[string]string
		want string
	}{
		{"lease equals full budget", map[string]string{"LEASE": "20s"}, "lease"},
		{"lease below full budget", map[string]string{"LEASE": "19.999999999s"}, "lease"},
		{"lease equals call timeout + margin", map[string]string{"LEASE": "10s", "CALL_TIMEOUT": "5s"}, "lease"},
		{"lease below call timeout + margin", map[string]string{"LEASE": "10s", "CALL_TIMEOUT": "6s"}, "lease"},
		{"lease below call timeout", map[string]string{"LEASE": "3s", "CALL_TIMEOUT": "5s"}, "lease"},
		{"default lease, long call timeout", map[string]string{"CALL_TIMEOUT": "25s"}, "lease"},
		{"zero call timeout", map[string]string{"CALL_TIMEOUT": "0s"}, "call timeout"},
		{"cap below base", map[string]string{"BACKOFF_BASE": "10s", "BACKOFF_CAP": "1s"}, "cap"},
		{"zero max attempts", map[string]string{"MAX_ATTEMPTS": "0"}, "max attempts"},
		{"bad duration", map[string]string{"LEASE": "thirty"}, "LEASE"},
		{"bad integer", map[string]string{"WORKERS": "four"}, "WORKERS"},
		{"zero workers", map[string]string{"WORKERS": "0"}, "WORKERS"},
		{"negative drain timeout", map[string]string{"DRAIN_TIMEOUT": "-1s"}, "DRAIN_TIMEOUT"},
		{"bad mock-saas URL", map[string]string{"MOCKSAAS_URL": "mock-saas:8080"}, "MOCKSAAS_URL"},
		{"bad mongo URI", map[string]string{"MONGO_URI": "localhost:27017"}, "MONGO_URI"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := loadConfig(env(tc.vars), host)
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("loadConfig = %v, want an error mentioning %q", err, tc.want)
			}
		})
	}
	if _, err := loadConfig(env(map[string]string{"LEASE": "20.000000001s", "CALL_TIMEOUT": "5s"}), host); err != nil {
		t.Fatalf("lease just above full budget rejected: %v", err)
	}
	if _, err := loadConfig(env(nil), func() (string, error) { return "", errors.New("no hostname") }); err == nil ||
		!strings.Contains(err.Error(), "WORKER_ID") {
		t.Fatalf("missing worker id: err = %v", err)
	}
}
