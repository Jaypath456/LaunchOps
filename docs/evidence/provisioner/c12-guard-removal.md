# mock-saas orphaned-claim recovery: guard-removal results (c12)

Each guard in `provisioner/internal/mocksaas/service.go` was removed on its own and `go test -race -count=1 -v ./internal/mocksaas/` was run
against real MongoDB (mongo:8). Every removal made at least one test fail (exit status 1). After each run `service.go` was restored from a copy
and its sha256 (`91dbb107b485eb335789a0d63ad75ece4846f8c5def883ac90fac26cfaaf5d51`) was checked against the value from before the experiments
(`raw/sha256.txt`). The full passing run with every guard present is `c12/output.txt` (all packages `ok`, 0 SKIP, 0 FAIL, 80 top-level tests passed).
Environment: `c12/ENV.txt`. Commands: `c12/COMMAND.txt`. Per guard: `c12/raw/<name>.diff` (the exact removal) and `c12/raw/<name>.txt` (output).

| # | Guard removed | Where | Tests that failed without it |
|---|---|---|---|
| 1 | `claim: stored.Claim` | takeover compare-and-set filter in `existing()` | `TestConcurrentTakeoversOneWins`, `TestStuckClaimConcurrentRequestsCreateOneAccount` |
| 2 | `time.Since(stored.StartedAt) < s.cfg.ProcessingTimeout` | staleness check in `existing()` | `TestCrashAfterClaimBeforeAccountInsert`, `TestCrashAfterAccountInsertBeforeResponseStored`, `TestProcessingTimeoutBoundary`, `TestProcessingReturnsConflict`, `TestConcurrentSameKey`, `TestStuckClaimConcurrentRequestsCreateOneAccount` |
| 3 | `state: processing` | takeover compare-and-set filter in `existing()` | `TestTakeoverDoesNotTouchCompletedRow` |
| 4 | `state: processing` and `claim` | completion filter in `finish()` | `TestStalledOriginalCannotOverwriteTakeover` |

What each test checks:
- **1** `TestConcurrentTakeoversOneWins`: two requests read the stale row, rendezvous in the `BeforeTakeover` hook, then both run the compare-and-set.
  With the guard exactly one gets 201 and the other 409 "in progress"; without it both match and proceed (the loser then fails at completion with 503).
- **2** `TestProcessingTimeoutBoundary`: a claim 2 s old (timeout 10 s) must get 409; without the check it is taken over and returns 201.
  The two crash tests fail at their "retry before the timeout gets 409" step the same way.
- **3** `TestTakeoverDoesNotTouchCompletedRow`: the row becomes `done` between the stale read and the compare-and-set; with the guard the request gets 409
  and the row is untouched, without it the takeover matches a `done` row and the request ends in 503.
- **4** `TestStalledOriginalCannotOverwriteTakeover`: the original is held after its account insert, a takeover completes, then the original resumes. With
  the guard its completion matches nothing (503) and the takeover's result stands; without it the original overwrites the finished row and returns 201.

Overlap: in the completion filter (4) `state` and `claim` each block the stalled original on their own, so they were removed together. In the takeover
filter (1 and 3) the two conditions guard different races and are tested separately.

Other tests that pass with all guards present: crash after the claim and before the account insert, crash after the account insert and before the
response is stored (each: retry before the timeout is 409, retry after returns the original account with a replayed stored response), 50 concurrent
same-key requests on a stuck row create one account, different body still 422 on young and stale rows, an account under another key stays a permanent
409 conflict, and `TestInterruptedClaimRecoveredThroughRetries` (worker: interrupted first attempt, job succeeds through retries with one account).
