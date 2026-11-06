package mocksaas

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math/rand/v2"
	"net"
	"net/http"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

type Service struct {
	db  *mongo.Database
	cfg Config
	log *slog.Logger
	mux *http.ServeMux
}

type accountInput struct {
	Username    string `json:"username" bson:"username"`
	DisplayName string `json:"display_name" bson:"display_name"`
	Department  string `json:"department" bson:"department"`
}

type Account struct {
	ExternalRef string `json:"external_ref" bson:"_id"`
	Connector   string `json:"connector" bson:"connector"`
	Username    string `json:"username" bson:"username"`
	DisplayName string `json:"display_name" bson:"display_name"`
	Department  string `json:"department" bson:"department"`
}

type requestRecord struct {
	Key         string `bson:"key"`
	Fingerprint string `bson:"fingerprint"`
	State       string `bson:"state"`
	Status      int    `bson:"status"`
	Body        []byte `bson:"body"`
}

type Stats struct {
	Created              int64 `json:"created" bson:"-"`
	Replayed             int64 `json:"replayed" bson:"replayed"`
	InProgressConflicts  int64 `json:"in_progress_conflicts" bson:"in_progress_conflicts"`
	ErrorFired           int64 `json:"error_fired" bson:"error_fired"`
	HangFired            int64 `json:"hang_fired" bson:"hang_fired"`
	DropAfterCommitFired int64 `json:"drop_after_commit_fired" bson:"drop_after_commit_fired"`
}

// New constructs an in-process HTTP handler and creates the required unique indexes.
// The caller owns db.Client() and must disconnect it after serving has stopped.
func New(ctx context.Context, db *mongo.Database, cfg Config, logger *slog.Logger) (*Service, error) {
	cfg = cfg.defaults()
	if err := cfg.validate(); err != nil {
		return nil, err
	}
	if db == nil {
		return nil, errors.New("MongoDB database is required")
	}
	if logger == nil {
		logger = slog.New(slog.NewJSONHandler(io.Discard, nil))
	}
	s := &Service{db: db, cfg: cfg, log: logger, mux: http.NewServeMux()}
	ctx, cancel := context.WithTimeout(ctx, cfg.OperationTimeout)
	defer cancel()
	if _, err := db.Collection("requests").Indexes().CreateOne(ctx, mongo.IndexModel{
		Keys: bson.D{{Key: "key", Value: 1}}, Options: options.Index().SetUnique(true),
	}); err != nil {
		return nil, fmt.Errorf("request index: %w", err)
	}
	if _, err := db.Collection("accounts").Indexes().CreateOne(ctx, mongo.IndexModel{
		Keys: bson.D{{Key: "connector", Value: 1}, {Key: "username", Value: 1}}, Options: options.Index().SetUnique(true),
	}); err != nil {
		return nil, fmt.Errorf("account index: %w", err)
	}
	if _, err := db.Collection("settings").UpdateOne(ctx, bson.M{"_id": "faults"},
		bson.M{"$setOnInsert": cfg.Faults}, options.UpdateOne().SetUpsert(true)); err != nil {
		return nil, fmt.Errorf("fault defaults: %w", err)
	}
	s.mux.HandleFunc("POST /v1/{connector}/accounts", s.createAccount)
	s.mux.HandleFunc("GET /admin/accounts", s.accounts)
	s.mux.HandleFunc("GET /admin/stats", s.stats)
	s.mux.HandleFunc("POST /admin/faults", s.faults)
	s.mux.HandleFunc("GET /healthz", s.health)
	return s, nil
}

func (s *Service) ServeHTTP(w http.ResponseWriter, r *http.Request) { s.mux.ServeHTTP(w, r) }

// Serve drains active requests on cancellation, then forcibly closes requests that
// exceed the shutdown deadline (including injected hangs).
func (s *Service) Serve(ctx context.Context, listener net.Listener) error {
	server := &http.Server{
		Handler: s, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second,
		WriteTimeout: s.cfg.HangDuration + s.cfg.OperationTimeout + 5*time.Second,
		IdleTimeout:  time.Minute,
		ErrorLog:     slog.NewLogLogger(s.log.Handler(), slog.LevelError),
	}
	finished := make(chan struct{})
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		select {
		case <-ctx.Done():
			shutdownCtx, cancel := context.WithTimeout(context.Background(), s.cfg.ShutdownTimeout)
			defer cancel()
			if err := server.Shutdown(shutdownCtx); err != nil {
				s.log.Warn("shutdown deadline exceeded", "error", err)
				_ = server.Close()
			}
		case <-finished:
		}
	}()
	err := server.Serve(listener)
	close(finished)
	<-stopped
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func (s *Service) fail(w http.ResponseWriter, err error) {
	s.log.Error("mock SaaS operation failed", "error", err)
	writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "storage unavailable"})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	body, _ := json.Marshal(value)
	writeResponse(w, status, body)
}

func writeResponse(w http.ResponseWriter, status int, body []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

func decode(w http.ResponseWriter, r *http.Request, target any) error {
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return errors.New("expected one JSON object")
	}
	return nil
}

func (s *Service) increment(ctx context.Context, name string) error {
	_, err := s.db.Collection("stats").UpdateOne(ctx, bson.M{"_id": "stats"},
		bson.M{"$inc": bson.M{name: 1}}, options.UpdateOne().SetUpsert(true))
	return err
}

func (s *Service) createAccount(w http.ResponseWriter, r *http.Request) {
	// Faults precede validation and the atomic claim: an injected 503 does no work.
	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.OperationTimeout)
	var faults Faults
	err := s.db.Collection("settings").FindOne(ctx, bson.M{"_id": "faults"}).Decode(&faults)
	cancel()
	if err != nil {
		s.fail(w, err)
		return
	}
	if rand.Float64() < faults.ErrorRate {
		ctx, cancel := context.WithTimeout(r.Context(), s.cfg.OperationTimeout)
		defer cancel()
		if err := s.increment(ctx, "error_fired"); err != nil {
			s.fail(w, err)
			return
		}
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "injected error"})
		return
	}
	if rand.Float64() < faults.HangRate {
		// Reaching EOF lets net/http monitor the connection for a disconnect while
		// we wait. Leaving a POST body unread can hide client cancellation.
		if _, err := io.Copy(io.Discard, http.MaxBytesReader(w, r.Body, 64<<10)); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), s.cfg.OperationTimeout)
		err := s.increment(ctx, "hang_fired")
		cancel()
		if err != nil {
			s.fail(w, err)
			return
		}
		timer := time.NewTimer(s.cfg.HangDuration)
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-r.Context().Done():
			return
		}
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "injected hang"})
		return
	}
	key := r.Header.Get("Idempotency-Key")
	var input accountInput
	if key == "" || decode(w, r, &input) != nil || input.Username == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Idempotency-Key and a valid account body with username are required"})
		return
	}
	connector := r.PathValue("connector")
	canonical, _ := json.Marshal(struct {
		Connector string
		Input     accountInput
	}{connector, input})
	hash := sha256.Sum256(canonical)
	fingerprint := hex.EncodeToString(hash[:])
	ctx, cancel = context.WithTimeout(r.Context(), s.cfg.OperationTimeout)
	defer cancel()
	requests := s.db.Collection("requests")
	_, err = requests.InsertOne(ctx, requestRecord{Key: key, Fingerprint: fingerprint, State: "processing"})
	if mongo.IsDuplicateKeyError(err) {
		s.replay(ctx, w, key, fingerprint)
		return
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	account := Account{bson.NewObjectID().Hex(), connector, input.Username, input.DisplayName, input.Department}
	_, err = s.db.Collection("accounts").InsertOne(ctx, account)
	status := http.StatusCreated
	body, _ := json.Marshal(account)
	if mongo.IsDuplicateKeyError(err) {
		status = http.StatusConflict
		body = []byte(`{"error":"account exists"}`)
	} else if err != nil {
		s.fail(w, err)
		return
	}
	result, err := requests.UpdateOne(ctx, bson.M{"key": key, "state": "processing"},
		bson.M{"$set": bson.M{"state": "done", "status": status, "body": body}})
	if err != nil {
		s.fail(w, err)
		return
	}
	if result.MatchedCount != 1 {
		s.fail(w, errors.New("request completion lost its processing claim"))
		return
	}
	if status == http.StatusCreated && rand.Float64() < faults.DropAfterCommitRate {
		if err := s.increment(ctx, "drop_after_commit_fired"); err != nil {
			s.fail(w, err)
			return
		}
		conn, _, err := http.NewResponseController(w).Hijack()
		if err != nil {
			s.log.Error("drop after commit requires HTTP hijacking", "error", err)
			panic(http.ErrAbortHandler)
		}
		_ = conn.Close()
		return
	}
	writeResponse(w, status, body)
}

func (s *Service) replay(ctx context.Context, w http.ResponseWriter, key, fingerprint string) {
	var stored requestRecord
	if err := s.db.Collection("requests").FindOne(ctx, bson.M{"key": key}).Decode(&stored); err != nil {
		s.fail(w, err)
		return
	}
	if stored.Fingerprint != fingerprint {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "idempotency key body mismatch"})
		return
	}
	if stored.State == "processing" {
		if err := s.increment(ctx, "in_progress_conflicts"); err != nil {
			s.fail(w, err)
			return
		}
		writeJSON(w, http.StatusConflict, map[string]string{"error": "in progress"})
		return
	}
	if stored.State != "done" {
		s.fail(w, errors.New("invalid stored request state"))
		return
	}
	if err := s.increment(ctx, "replayed"); err != nil {
		s.fail(w, err)
		return
	}
	writeResponse(w, stored.Status, stored.Body)
}

func (s *Service) accounts(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.OperationTimeout)
	defer cancel()
	cursor, err := s.db.Collection("accounts").Find(ctx, bson.D{}, options.Find().SetSort(bson.D{{Key: "_id", Value: 1}}))
	if err != nil {
		s.fail(w, err)
		return
	}
	defer cursor.Close(ctx)
	accounts := []Account{}
	if err := cursor.All(ctx, &accounts); err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, accounts)
}

func (s *Service) stats(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.OperationTimeout)
	defer cancel()
	var stats Stats
	err := s.db.Collection("stats").FindOne(ctx, bson.M{"_id": "stats"}).Decode(&stats)
	if err != nil && !errors.Is(err, mongo.ErrNoDocuments) {
		s.fail(w, err)
		return
	}
	stats.Created, err = s.db.Collection("accounts").CountDocuments(ctx, bson.D{})
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, stats)
}

func (s *Service) faults(w http.ResponseWriter, r *http.Request) {
	// Apply only supplied fields in one write, so concurrent partial updates cannot
	// overwrite unrelated settings. Invalid updates change nothing.
	var patch map[string]*float64
	if err := decode(w, r, &patch); err != nil || patch == nil || len(patch) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "expected fault rates"})
		return
	}
	for key, rate := range patch {
		if key != "error_rate" && key != "hang_rate" && key != "drop_after_commit_rate" {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "unknown fault rate"})
			return
		}
		if rate == nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "fault rates cannot be null"})
			return
		}
		if err := (Faults{ErrorRate: *rate}).validate(); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.OperationTimeout)
	defer cancel()
	var faults Faults
	err := s.db.Collection("settings").FindOneAndUpdate(ctx, bson.M{"_id": "faults"},
		bson.M{"$set": patch}, options.FindOneAndUpdate().SetReturnDocument(options.After)).Decode(&faults)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, faults)
}

func (s *Service) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.OperationTimeout)
	defer cancel()
	if err := s.db.Client().Ping(ctx, nil); err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
