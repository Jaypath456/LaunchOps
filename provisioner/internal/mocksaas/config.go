// Package mocksaas simulates external account APIs. It is not a real integration.
package mocksaas

import (
	"fmt"
	"math"
	"os"
	"strconv"
	"time"
)

type Faults struct {
	ErrorRate           float64 `json:"error_rate" bson:"error_rate"`
	HangRate            float64 `json:"hang_rate" bson:"hang_rate"`
	DropAfterCommitRate float64 `json:"drop_after_commit_rate" bson:"drop_after_commit_rate"`
	LatencyMS           int     `json:"latency_ms" bson:"latency_ms"`
}

func (f Faults) validate() error {
	if f.LatencyMS < 0 || f.LatencyMS > 10000 {
		return fmt.Errorf("latency_ms must be between 0 and 10000")
	}
	for _, rate := range []float64{f.ErrorRate, f.HangRate, f.DropAfterCommitRate} {
		if math.IsNaN(rate) || math.IsInf(rate, 0) || rate < 0 || rate > 1 {
			return fmt.Errorf("fault rates must be finite and between 0 and 1")
		}
	}
	return nil
}

// Hooks are test seams; they are nil in production.
type Hooks struct {
	// CrashAt is asked at the stages "after_claim" and "after_account_insert". Returning true aborts the
	// handler with no further writes and no response, leaving the database as a crash would.
	CrashAt func(stage string) bool
	// BeforeTakeover runs after a stale claim was read and before the takeover compare-and-set.
	BeforeTakeover func()
}

type Config struct {
	MongoURI         string
	Database         string
	Address          string
	Faults           Faults
	HangDuration     time.Duration
	OperationTimeout time.Duration
	ShutdownTimeout  time.Duration
	// ProcessingTimeout is how long a `processing` request row is respected before a request with the same
	// key and body may take it over. It should exceed OperationTimeout so a live original has hit its own
	// deadline; correctness does not depend on it (the fixed account id and unique indexes do).
	ProcessingTimeout time.Duration
	Hooks             Hooks
}

func (c Config) defaults() Config {
	if c.MongoURI == "" {
		c.MongoURI = "mongodb://localhost:27017"
	}
	if c.Database == "" {
		c.Database = "mocksaas"
	}
	if c.Address == "" {
		c.Address = ":8080"
	}
	if c.HangDuration == 0 {
		c.HangDuration = time.Minute
	}
	if c.OperationTimeout == 0 {
		c.OperationTimeout = 5 * time.Second
	}
	if c.ShutdownTimeout == 0 {
		c.ShutdownTimeout = 10 * time.Second
	}
	if c.ProcessingTimeout == 0 {
		c.ProcessingTimeout = 10 * time.Second
	}
	return c
}

func (c Config) validate() error {
	if c.HangDuration <= 0 || c.OperationTimeout <= 0 || c.ShutdownTimeout <= 0 || c.ProcessingTimeout <= 0 {
		return fmt.Errorf("timeouts must be positive")
	}
	return c.Faults.validate()
}

// ConfigFromEnv reads startup defaults. Runtime fault settings are persisted in MongoDB.
func ConfigFromEnv() (Config, error) {
	c := Config{MongoURI: os.Getenv("MONGO_URI"), Database: os.Getenv("MOCKSAAS_DB"), Address: os.Getenv("MOCKSAAS_ADDR")}.defaults()
	if value, ok := os.LookupEnv("LATENCY_MS"); ok {
		parsed, err := strconv.Atoi(value)
		if err != nil {
			return Config{}, fmt.Errorf("LATENCY_MS: %w", err)
		}
		c.Faults.LatencyMS = parsed
	}
	for name, target := range map[string]*float64{
		"ERROR_RATE":             &c.Faults.ErrorRate,
		"HANG_RATE":              &c.Faults.HangRate,
		"DROP_AFTER_COMMIT_RATE": &c.Faults.DropAfterCommitRate,
	} {
		if value, ok := os.LookupEnv(name); ok {
			parsed, err := strconv.ParseFloat(value, 64)
			if err != nil {
				return Config{}, fmt.Errorf("%s: %w", name, err)
			}
			*target = parsed
		}
	}
	for name, target := range map[string]*time.Duration{
		"MOCKSAAS_HANG_DURATION":     &c.HangDuration,
		"MOCKSAAS_OPERATION_TIMEOUT": &c.OperationTimeout,
		"MOCKSAAS_SHUTDOWN_TIMEOUT":  &c.ShutdownTimeout,
		"PROCESSING_TIMEOUT":         &c.ProcessingTimeout,
	} {
		if value, ok := os.LookupEnv(name); ok {
			parsed, err := time.ParseDuration(value)
			if err != nil {
				return Config{}, fmt.Errorf("%s: %w", name, err)
			}
			*target = parsed
		}
	}
	return c, c.validate()
}
