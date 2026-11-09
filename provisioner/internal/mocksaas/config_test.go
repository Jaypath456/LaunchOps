package mocksaas

import (
	"math"
	"testing"
	"time"
)

func TestConfigFromEnv(t *testing.T) {
	for _, name := range []string{"MONGO_URI", "MOCKSAAS_DB", "MOCKSAAS_ADDR", "ERROR_RATE", "HANG_RATE", "DROP_AFTER_COMMIT_RATE", "MOCKSAAS_HANG_DURATION", "MOCKSAAS_OPERATION_TIMEOUT", "MOCKSAAS_SHUTDOWN_TIMEOUT", "PROCESSING_TIMEOUT"} {
		t.Setenv(name, "")
	}
	// Empty numeric/duration variables are invalid rather than silently defaulted.
	if _, err := ConfigFromEnv(); err == nil {
		t.Fatal("empty rate accepted")
	}
	t.Setenv("MONGO_URI", "mongodb://example:27017")
	t.Setenv("MOCKSAAS_DB", "custom")
	t.Setenv("MOCKSAAS_ADDR", ":9090")
	t.Setenv("ERROR_RATE", "0")
	t.Setenv("HANG_RATE", "0.25")
	t.Setenv("DROP_AFTER_COMMIT_RATE", "1")
	t.Setenv("MOCKSAAS_HANG_DURATION", "2m")
	t.Setenv("MOCKSAAS_OPERATION_TIMEOUT", "3s")
	t.Setenv("MOCKSAAS_SHUTDOWN_TIMEOUT", "4s")
	t.Setenv("PROCESSING_TIMEOUT", "7s")
	cfg, err := ConfigFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.MongoURI != "mongodb://example:27017" || cfg.Database != "custom" || cfg.Address != ":9090" || cfg.Faults != (Faults{HangRate: 0.25, DropAfterCommitRate: 1}) || cfg.HangDuration != 2*time.Minute || cfg.OperationTimeout != 3*time.Second || cfg.ShutdownTimeout != 4*time.Second || cfg.ProcessingTimeout != 7*time.Second {
		t.Fatalf("config = %+v", cfg)
	}
	for _, value := range []string{"-1", "1.01", "NaN", "+Inf", "bad"} {
		t.Setenv("ERROR_RATE", value)
		if _, err := ConfigFromEnv(); err == nil {
			t.Fatalf("accepted rate %s", value)
		}
	}
	t.Setenv("ERROR_RATE", "0")
	for _, name := range []string{"MOCKSAAS_HANG_DURATION", "PROCESSING_TIMEOUT"} {
		for _, value := range []string{"bad", "0s", "-1s"} {
			t.Setenv(name, value)
			if _, err := ConfigFromEnv(); err == nil {
				t.Fatalf("accepted %s=%s", name, value)
			}
		}
		t.Setenv(name, "5s")
	}
}

func TestConfigDefaultsAndValidation(t *testing.T) {
	cfg := (Config{}).defaults()
	if err := cfg.validate(); err != nil {
		t.Fatal(err)
	}
	if cfg.Database != "mocksaas" || cfg.MongoURI == "" || cfg.Address == "" || cfg.HangDuration <= 0 || cfg.OperationTimeout <= 0 || cfg.ShutdownTimeout <= 0 || cfg.ProcessingTimeout != 10*time.Second {
		t.Fatalf("defaults = %+v", cfg)
	}
	for _, rate := range []float64{-1, 2, math.NaN(), math.Inf(1)} {
		if err := (Faults{HangRate: rate}).validate(); err == nil {
			t.Fatal("invalid rate accepted")
		}
	}
}
