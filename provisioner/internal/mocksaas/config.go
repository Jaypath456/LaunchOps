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
}

func (f Faults) validate() error {
	for _, rate := range []float64{f.ErrorRate, f.HangRate, f.DropAfterCommitRate} {
		if math.IsNaN(rate) || math.IsInf(rate, 0) || rate < 0 || rate > 1 {
			return fmt.Errorf("fault rates must be finite and between 0 and 1")
		}
	}
	return nil
}

type Config struct {
	MongoURI         string
	Database         string
	Address          string
	Faults           Faults
	HangDuration     time.Duration
	OperationTimeout time.Duration
	ShutdownTimeout  time.Duration
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
	return c
}

func (c Config) validate() error {
	if c.HangDuration <= 0 || c.OperationTimeout <= 0 || c.ShutdownTimeout <= 0 {
		return fmt.Errorf("timeouts must be positive")
	}
	return c.Faults.validate()
}

// ConfigFromEnv reads startup defaults. Runtime fault settings are persisted in MongoDB.
func ConfigFromEnv() (Config, error) {
	c := Config{MongoURI: os.Getenv("MONGO_URI"), Database: os.Getenv("MOCKSAAS_DB"), Address: os.Getenv("MOCKSAAS_ADDR")}.defaults()
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
