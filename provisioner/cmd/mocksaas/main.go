package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"syscall"

	"launchops/provisioner/internal/mocksaas"

	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	flags := flag.NewFlagSet(os.Args[0], flag.ExitOnError)
	check := flags.Bool("healthcheck", false, "GET /healthz and exit")
	flags.Parse(os.Args[1:])
	if *check {
		address := os.Getenv("MOCKSAAS_ADDR")
		if address == "" {
			address = ":8080"
		}
		if err := healthcheck(address); err != nil {
			logger.Error("healthcheck failed", "error", err.Error())
			os.Exit(1)
		}
		return
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if err := run(ctx, logger); err != nil {
		logger.Error("mock SaaS stopped", "error", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, logger *slog.Logger) error {
	cfg, err := mocksaas.ConfigFromEnv()
	if err != nil {
		return err
	}
	client, err := mongo.Connect(options.Client().ApplyURI(cfg.MongoURI).
		SetConnectTimeout(cfg.OperationTimeout).SetServerSelectionTimeout(cfg.OperationTimeout))
	if err != nil {
		return fmt.Errorf("MongoDB client: %w", err)
	}
	defer func() {
		closeCtx, cancel := context.WithTimeout(context.Background(), cfg.OperationTimeout)
		defer cancel()
		if err := client.Disconnect(closeCtx); err != nil {
			logger.Error("MongoDB disconnect failed", "error", err)
		}
	}()
	service, err := mocksaas.New(ctx, client.Database(cfg.Database), cfg, logger)
	if err != nil {
		return err
	}
	listener, err := net.Listen("tcp", cfg.Address)
	if err != nil {
		return err
	}
	logger.Info("mock SaaS simulator listening", "address", listener.Addr().String(), "database", cfg.Database)
	if err := service.Serve(ctx, listener); err != nil {
		return err
	}
	logger.Info("mock SaaS simulator stopped")
	return nil
}
