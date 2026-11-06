package main

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"os"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

type listeningLog struct{ address chan string }

func (l listeningLog) Write(p []byte) (int, error) {
	var entry struct {
		Address string `json:"address"`
	}
	if err := json.Unmarshal(p, &entry); err != nil {
		return 0, err
	}
	if entry.Address != "" {
		l.address <- entry.Address
	}
	return len(p), nil
}

func TestRunGracefulShutdown(t *testing.T) {
	uri := os.Getenv("MONGO_URI")
	if uri == "" {
		t.Skip("MONGO_URI is required for real MongoDB tests")
	}
	database := "mocksaas_test_" + bson.NewObjectID().Hex()
	t.Setenv("MONGO_URI", uri)
	t.Setenv("MOCKSAAS_DB", database)
	t.Setenv("MOCKSAAS_ADDR", "127.0.0.1:0")
	for _, name := range []string{"ERROR_RATE", "HANG_RATE", "DROP_AFTER_COMMIT_RATE"} {
		t.Setenv(name, "0")
	}
	for _, name := range []string{"MOCKSAAS_OPERATION_TIMEOUT", "MOCKSAAS_SHUTDOWN_TIMEOUT"} {
		t.Setenv(name, "5s")
	}
	t.Setenv("MOCKSAAS_HANG_DURATION", "1m")
	client, err := mongo.Connect(options.Client().ApplyURI(uri).SetConnectTimeout(5 * time.Second).SetServerSelectionTimeout(5 * time.Second))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := client.Database(database).Drop(ctx); err != nil {
			t.Error(err)
		}
		if err := client.Disconnect(ctx); err != nil {
			t.Error(err)
		}
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	addresses := make(chan string, 1)
	done := make(chan error, 1)
	go func() { done <- run(ctx, slog.New(slog.NewJSONHandler(listeningLog{addresses}, nil))) }()
	var address string
	select {
	case address = <-addresses:
	case err := <-done:
		t.Fatalf("startup failed: %v", err)
	case <-time.After(10 * time.Second):
		t.Fatal("startup timed out")
	}
	httpClient := &http.Client{Timeout: 5 * time.Second}
	response, err := httpClient.Get("http://" + address + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	_, readErr := io.Copy(io.Discard, response.Body)
	response.Body.Close()
	if readErr != nil {
		t.Fatal(readErr)
	}
	if response.StatusCode != http.StatusOK {
		t.Fatalf("health status = %d", response.StatusCode)
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("shutdown timed out")
	}
	response, err = httpClient.Get("http://" + address + "/healthz")
	if response != nil {
		response.Body.Close()
	}
	if err == nil {
		t.Fatal("server still listening")
	}
}
