package jobs

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"go.mongodb.org/mongo-driver/v2/bson"
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
			if string(job.Status) != status {
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

// The invalid workflow fixture has valid individual jobs but repeats a connector.
// Django rejects it at creation; direct inserts must still respect element guards.
func TestDuplicateConnectorFixtureTransitions(t *testing.T) {
	source, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "invalid_workflow_duplicate_connectors.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Provisioning []Job `json:"provisioning"`
	}
	if err := json.Unmarshal(source, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Provisioning) != 2 || fixture.Provisioning[0].Connector != fixture.Provisioning[1].Connector {
		t.Fatal("invalid workflow fixture must repeat a connector")
	}
	e := newEnv(t, validPolicy())
	for _, finish := range []string{"succeed", "retry", "dead"} {
		t.Run(finish, func(t *testing.T) {
			id := bson.NewObjectID()
			if _, err := e.coll.InsertOne(testContext(t), bson.D{
				{Key: "_id", Value: id}, {Key: "provisioning", Value: fixture.Provisioning},
				{Key: "events", Value: bson.A{}},
			}); err != nil {
				t.Fatal(err)
			}
			before := e.load(t, id)
			first := before.Provisioning[0]
			claim := e.claim(t, Candidate{InstanceID: id, Connector: first.Connector,
				SeenStatus: first.Status, SeenFence: first.Fence}, "worker", t0)
			afterClaim := e.load(t, id)
			if !reflect.DeepEqual(afterClaim.Provisioning[1], before.Provisioning[1]) {
				t.Fatal("claim modified duplicate connector outside its guard")
			}
			if finish == "succeed" {
				err = e.store.Succeed(testContext(t), claim, "account")
			} else {
				err = e.store.RecordFailure(testContext(t), claim, "failure", finish == "retry", validPolicy(), t0)
			}
			if err != nil {
				t.Fatal(err)
			}
			after := e.load(t, id)
			if !reflect.DeepEqual(after.Provisioning[1], before.Provisioning[1]) {
				t.Fatal("finish modified duplicate connector outside its guard")
			}
			want := map[string]Status{"succeed": StatusSucceeded, "retry": StatusRetry, "dead": StatusDead}[finish]
			if after.Provisioning[0].Status != want || len(after.Events) != 1 {
				t.Fatalf("finish did not transition the guarded element: %+v", after)
			}
		})
	}
}
