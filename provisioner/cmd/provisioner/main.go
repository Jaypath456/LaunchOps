// Command provisioner runs provisioning jobs from workflow_instances against mock-saas (a simulator,
// not a real integration).
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"launchops/provisioner/internal/connectors"
	"launchops/provisioner/internal/jobs"
	"launchops/provisioner/internal/worker"

	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
	"go.mongodb.org/mongo-driver/v2/mongo/readpref"
)

const (
	collection     = "workflow_instances"
	opTimeout      = 5 * time.Second // every MongoDB call made by the store
	pingTimeout    = 2 * time.Second
	closeTimeout   = 5 * time.Second
	connectTimeout = 5 * time.Second
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	flags := flag.NewFlagSet(os.Args[0], flag.ExitOnError)
	check := flags.Bool("healthcheck", false, "GET /healthz and exit")
	flags.Parse(os.Args[1:])
	if *check {
		address := os.Getenv("HEALTH_ADDR")
		if address == "" {
			address = ":8081"
		}
		if err := healthcheck(address); err != nil {
			logger.Error("healthcheck failed", "error", err.Error())
			os.Exit(1)
		}
		return
	}
	cfg, err := loadConfig(os.LookupEnv, os.Hostname)
	if err != nil {
		logger.Error("invalid configuration", "error", err.Error())
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	if err := run(ctx, cfg, logger); err != nil {
		logger.Error("provisioner stopped", "error", err.Error())
		stop()
		os.Exit(1)
	}
	logger.Info("provisioner stopped")
}

func run(ctx context.Context, cfg Config, logger *slog.Logger) error {
	client, err := mongo.Connect(options.Client().ApplyURI(cfg.MongoURI).
		SetConnectTimeout(connectTimeout).SetServerSelectionTimeout(connectTimeout))
	if err != nil {
		return fmt.Errorf("MongoDB client: %w", err)
	}
	defer func() {
		closeCtx, cancel := context.WithTimeout(context.Background(), closeTimeout)
		defer cancel()
		if err := client.Disconnect(closeCtx); err != nil {
			logger.Error("MongoDB disconnect failed", "error", err.Error())
		}
	}()
	store, err := jobs.NewStore(client, cfg.MongoDB, collection, jobs.StoreConfig{
		Policy: cfg.Policy, OpTimeout: opTimeout, Logger: logger.With("component", "store"),
	})
	if err != nil {
		return err
	}
	conn, err := connectors.NewHTTP(connectors.HTTPConfig{BaseURL: cfg.MockSaaSURL, Timeout: cfg.Policy.CallTimeout})
	if err != nil {
		return err
	}
	defer conn.CloseIdleConnections()

	listener, err := net.Listen("tcp", cfg.HealthAddr)
	if err != nil {
		return fmt.Errorf("health listener: %w", err)
	}
	health := &http.Server{
		Handler:           healthHandler(pingFunc(func(ctx context.Context) error { return client.Ping(ctx, readpref.Primary()) }), pingTimeout),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: time.Minute,
		ErrorLog: slog.NewLogLogger(logger.Handler(), slog.LevelError),
	}
	healthDone := make(chan error, 1)
	go func() { healthDone <- health.Serve(listener) }()

	pool := &worker.Pool{
		Store: store, Connector: conn, Policy: cfg.Policy, WorkerID: cfg.WorkerID, Workers: cfg.Workers,
		Logger: logger.With("component", "worker"), DrainTimeout: cfg.DrainTimeout,
	}
	logger.Info("provisioner started", "worker_id", cfg.WorkerID, "workers", cfg.Workers, "lease", cfg.Policy.Lease.String(),
		"call_timeout", cfg.Policy.CallTimeout.String(), "max_attempts", cfg.Policy.MaxAttempts, "health_addr", listener.Addr().String())
	runErr := pool.Run(ctx)

	// Health keeps answering during the drain; it stops after the pool.
	shutdownCtx, cancel := context.WithTimeout(context.Background(), closeTimeout)
	defer cancel()
	if err := health.Shutdown(shutdownCtx); err != nil {
		_ = health.Close()
	}
	if err := <-healthDone; err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Error("health server failed", "error", err.Error())
	}
	return runErr
}
