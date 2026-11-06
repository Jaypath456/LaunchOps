// Package connectors calls the mock-saas account API. mock-saas is a simulator, not a real integration.
package connectors

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	"launchops/provisioner/internal/jobs"
)

// maxBody caps how much of a response body is read.
const maxBody = 64 << 10

var connectorName = regexp.MustCompile(`^[a-z0-9_-]+$`)

// Request is one account creation. IdempotencyKey is the job's key and is the same on every attempt.
type Request struct {
	Connector      string
	IdempotencyKey string
	Payload        jobs.Payload
}

// Error is a failed call. Retryable tells the caller whether another attempt can succeed.
type Error struct {
	Retryable bool
	// Status is the HTTP status, or 0 when no response arrived.
	Status int
	Err    error
}

func (e *Error) Error() string { return e.Err.Error() }
func (e *Error) Unwrap() error { return e.Err }

// IsRetryable reports whether err allows another attempt. Errors that are not *Error count as retryable,
// so an unexpected failure is retried and ends dead after MaxAttempts rather than at once.
func IsRetryable(err error) bool {
	var e *Error
	if errors.As(err, &e) {
		return e.Retryable
	}
	return err != nil
}

// Classify decides whether a non-2xx response is retryable: 409 "in progress", 429 and 5xx are;
// every other status (other 4xx, and the unexpected 1xx/3xx) is permanent.
func Classify(status int, body []byte) bool {
	switch {
	case status == http.StatusConflict:
		var parsed struct {
			Error string `json:"error"`
		}
		return json.Unmarshal(body, &parsed) == nil && parsed.Error == "in progress"
	case status == http.StatusTooManyRequests:
		return true
	case status >= 500 && status <= 599:
		return true
	}
	return false
}

type HTTPConfig struct {
	// BaseURL is the mock-saas root, e.g. http://mock-saas:8080.
	BaseURL string
	// Timeout bounds every request, even when the caller's context has no deadline.
	Timeout time.Duration
}

// HTTP is one client for every connector: the connector name selects the path.
type HTTP struct {
	client    *http.Client
	transport *http.Transport
	base      *url.URL
	timeout   time.Duration
}

func NewHTTP(cfg HTTPConfig) (*HTTP, error) {
	base, err := url.Parse(cfg.BaseURL)
	if err != nil {
		return nil, fmt.Errorf("mock-saas URL: %w", err)
	}
	if (base.Scheme != "http" && base.Scheme != "https") || base.Host == "" {
		return nil, fmt.Errorf("mock-saas URL must be http(s)://host[:port], got %q", cfg.BaseURL)
	}
	if cfg.Timeout <= 0 {
		return nil, fmt.Errorf("connector timeout must be positive, got %v", cfg.Timeout)
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	return &HTTP{
		client: &http.Client{
			Transport: transport,
			// A POST is never re-sent to another location; a redirect is returned as a response.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
		transport: transport,
		base:      base,
		timeout:   cfg.Timeout,
	}, nil
}

// CloseIdleConnections closes kept-alive connections (used at shutdown).
func (c *HTTP) CloseIdleConnections() { c.transport.CloseIdleConnections() }

// CreateAccount posts the payload and returns the external reference.
func (c *HTTP) CreateAccount(ctx context.Context, req Request) (string, error) {
	prefix := "mock-saas " + req.Connector
	if !connectorName.MatchString(req.Connector) {
		return "", &Error{Err: fmt.Errorf("invalid connector name %q", req.Connector)}
	}
	if req.IdempotencyKey == "" {
		return "", &Error{Err: fmt.Errorf("%s: idempotency key is required", prefix)}
	}
	body, err := json.Marshal(map[string]string{
		"username": req.Payload.Username, "display_name": req.Payload.DisplayName, "department": req.Payload.Department,
	})
	if err != nil {
		return "", &Error{Err: fmt.Errorf("%s: encode body: %w", prefix, err)}
	}
	target := c.base.JoinPath("v1", req.Connector, "accounts")

	ctx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, target.String(), bytes.NewReader(body))
	if err != nil {
		return "", &Error{Err: fmt.Errorf("%s: build request: %w", prefix, err)}
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Idempotency-Key", req.IdempotencyKey)
	// net/http treats a request with Idempotency-Key as replayable and silently re-sends it on a new
	// connection when a reused one fails. Without GetBody the body cannot be rewound, so each job attempt
	// is exactly one HTTP request and every retry goes through the job state machine (attempts, backoff, events).
	httpReq.GetBody = nil

	resp, err := c.client.Do(httpReq)
	if err != nil {
		return "", c.transportError(ctx, prefix, "request", err)
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(io.LimitReader(resp.Body, maxBody))
	if err != nil {
		return "", c.transportError(ctx, prefix, fmt.Sprintf("read HTTP %d body", resp.StatusCode), err)
	}
	if resp.StatusCode >= 200 && resp.StatusCode <= 299 {
		var account struct {
			ExternalRef string `json:"external_ref"`
		}
		if err := json.Unmarshal(respBody, &account); err != nil || account.ExternalRef == "" {
			// The account may exist; a retry replays the stored response under the same key.
			return "", &Error{Retryable: true, Status: resp.StatusCode,
				Err: fmt.Errorf("%s: HTTP %d without external_ref: %s", prefix, resp.StatusCode, snippet(respBody))}
		}
		return account.ExternalRef, nil
	}
	return "", &Error{Retryable: Classify(resp.StatusCode, respBody), Status: resp.StatusCode,
		Err: fmt.Errorf("%s: HTTP %d: %s", prefix, resp.StatusCode, snippet(respBody))}
}

// transportError covers every failure without a complete response (deadline, refused, reset, closed
// without a response): all retryable.
func (c *HTTP) transportError(ctx context.Context, prefix, stage string, err error) error {
	if errors.Is(ctx.Err(), context.DeadlineExceeded) || errors.Is(err, context.DeadlineExceeded) {
		return &Error{Retryable: true, Err: fmt.Errorf("%s: %s: timeout: %w", prefix, stage, err)}
	}
	return &Error{Retryable: true, Err: fmt.Errorf("%s: %s: %w", prefix, stage, err)}
}

func snippet(body []byte) string {
	s := strings.TrimSpace(string(body))
	if len(s) > 200 {
		s = s[:200] + "..."
	}
	if s == "" {
		return "(empty body)"
	}
	return s
}
