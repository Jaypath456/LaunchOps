package jobs

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestProvisioningFixturesRoundTrip(t *testing.T) {
	fixtureDir := filepath.Join("..", "..", "..", "contracts", "fixtures")
	statuses := []string{"pending", "running", "retry", "succeeded", "dead", "overridden"}
	entries, err := os.ReadDir(fixtureDir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != len(statuses) {
		t.Fatalf("want %d fixtures, got %d", len(statuses), len(entries))
	}
	for _, status := range statuses {
		t.Run(status, func(t *testing.T) {
			source, err := os.ReadFile(filepath.Join(fixtureDir, status+".json"))
			if err != nil {
				t.Fatal(err)
			}
			var job Job
			decoder := json.NewDecoder(bytes.NewReader(source))
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&job); err != nil {
				t.Fatal(err)
			}
			if job.Status != status {
				t.Fatalf("fixture %s has status %s", status, job.Status)
			}
			encoded, err := json.Marshal(job)
			if err != nil {
				t.Fatal(err)
			}
			var before, after map[string]any
			if err := json.Unmarshal(source, &before); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(encoded, &after); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(before, after) {
				t.Fatalf("round trip changed contract fields:\nbefore: %s\nafter: %s", source, encoded)
			}
		})
	}
}
