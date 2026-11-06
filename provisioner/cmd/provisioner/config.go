package main

import (
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"time"

	"launchops/provisioner/internal/jobs"
	"launchops/provisioner/internal/worker"
)

type Config struct {
	MongoURI     string
	MongoDB      string
	Workers      int
	WorkerID     string
	Policy       jobs.Policy
	DrainTimeout time.Duration
	MockSaaSURL  string
	HealthAddr   string
}

// loadConfig reads the environment through lookup and applies defaults. Any unparsable value or a
// policy that fails jobs.Policy.Validate (Lease must exceed CallTimeout + LeaseMargin) is an error.
func loadConfig(lookup func(string) (string, bool), hostname func() (string, error)) (Config, error) {
	var errs []error
	str := func(name, def string) string {
		if v, ok := lookup(name); ok && strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
		return def
	}
	integer := func(name string, def int) int {
		v, ok := lookup(name)
		if !ok || strings.TrimSpace(v) == "" {
			return def
		}
		n, err := strconv.Atoi(strings.TrimSpace(v))
		if err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", name, err))
		}
		return n
	}
	duration := func(name string, def time.Duration) time.Duration {
		v, ok := lookup(name)
		if !ok || strings.TrimSpace(v) == "" {
			return def
		}
		d, err := time.ParseDuration(strings.TrimSpace(v))
		if err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", name, err))
		}
		return d
	}

	c := Config{
		MongoURI: str("MONGO_URI", "mongodb://localhost:27017"),
		MongoDB:  str("MONGO_DB", "launchops"),
		Workers:  integer("WORKERS", 4),
		WorkerID: str("WORKER_ID", ""),
		Policy: jobs.Policy{
			MaxAttempts: integer("MAX_ATTEMPTS", 5),
			Base:        duration("BACKOFF_BASE", time.Second),
			Cap:         duration("BACKOFF_CAP", 60*time.Second),
			Lease:       duration("LEASE", 30*time.Second),
			CallTimeout: duration("CALL_TIMEOUT", 5*time.Second),
		},
		DrainTimeout: duration("DRAIN_TIMEOUT", worker.DefaultDrainTimeout),
		MockSaaSURL:  str("MOCKSAAS_URL", "http://localhost:8080"),
		HealthAddr:   str("HEALTH_ADDR", ":8081"),
	}
	if c.WorkerID == "" {
		h, err := hostname()
		if err != nil || h == "" {
			errs = append(errs, fmt.Errorf("WORKER_ID is unset and hostname is unavailable: %v", err))
		}
		c.WorkerID = h
	}
	if c.Workers < 1 {
		errs = append(errs, fmt.Errorf("WORKERS must be at least 1, got %d", c.Workers))
	}
	if c.DrainTimeout < 0 {
		errs = append(errs, fmt.Errorf("DRAIN_TIMEOUT must not be negative, got %v", c.DrainTimeout))
	}
	if u, err := url.Parse(c.MockSaaSURL); err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		errs = append(errs, fmt.Errorf("MOCKSAAS_URL must be http(s)://host[:port], got %q", c.MockSaaSURL))
	}
	if !strings.HasPrefix(c.MongoURI, "mongodb://") && !strings.HasPrefix(c.MongoURI, "mongodb+srv://") {
		errs = append(errs, fmt.Errorf("MONGO_URI must start with mongodb:// or mongodb+srv://"))
	}
	if err := c.Policy.Validate(); err != nil {
		errs = append(errs, fmt.Errorf("policy (MAX_ATTEMPTS, BACKOFF_*, LEASE, CALL_TIMEOUT): %w", err))
	}
	if len(errs) > 0 {
		return Config{}, errors.Join(errs...)
	}
	return c, nil
}
