# Provisioner job store: guard-removal results (c11a)

Each compare-and-set guard in `provisioner/internal/jobs/store.go` was removed on its own, and the jobs test suite was run against
real MongoDB. Every removal made at least one test fail. After each run, `store.go` was restored from a copy, and its sha256
(`985bebef928dc272017db16e26d460752a25c2974c1122aa99af0c6faf10acda`) was checked against the value from before the experiment.
The full passing run with every guard present is in `output.txt`. The environment is in `ENV.txt` and the commands are in `COMMAND.txt`.

Command for each removal (from `provisioner/`):
`MONGO_URI=mongodb://localhost:47017 go test -race -count=1 -v ./internal/jobs/`
Raw output and the exact diff for each run: `guard-removal-raw/<name>.txt` and `guard-removal-raw/<name>.diff`.

## Summary

| # | Guard removed | Where | Tests that failed without it |
|---|---|---|---|
| 1 | `status: seen_status` | Claim `$elemMatch` (`claimFilter`) | `TestOverriddenNotClaimable`, `TestSucceededNotClaimable` |
| 2 | `fence: seen_fence` | Claim `$elemMatch` (`claimFilter`) | `TestStaleCandidateAfterManualRetry` |
| 3 | `fence: claim.Fence` | finish `$elemMatch` (`finishFilter`, used by Succeed and RecordFailure) | `TestStaleWorkerFenceRejected` |
| 4 | due condition (`next_attempt_at <= now` / `lease_until < now`) | Claim `$elemMatch` (`claimFilter`) | `TestLiveLeaseNotReclaimable`, `TestRetryNotDueNotClaimable` |

In all four runs, `go test` exited with status 1 and the package result was `FAIL`. Every other test still passed. Some guards overlap.
For example, after a winning claim the status and the fence both change, so `TestClaimConcurrentOneWinner` still passes when only
one of the two is removed. Because of that overlap, each guard has its own targeted test, and only that guard can stop the scenario
in it.

## 1. Claim status check

```diff
@@ -134,7 +134,6 @@
 	}
 	match := bson.D{
 		{Key: "connector", Value: c.Connector},
-		{Key: "status", Value: c.SeenStatus},
 		{Key: "fence", Value: c.SeenFence},
 	}
 	match = append(match, due)
```
Failures (ObjectIDs elided):
```
store_test.go:438: claim {... Connector:google SeenStatus:pending SeenFence:0} at 2026-11-05 09:00:00 +0000 UTC = ({... Attempt:1 Fence:1 WorkerID:intruder}, <nil>), want ErrClaimLost
--- FAIL: TestOverriddenNotClaimable
store_test.go:454: claim {... Connector:google SeenStatus:pending SeenFence:1} at 2026-11-05 10:00:00 +0000 UTC = ({... Attempt:2 Fence:2 WorkerID:intruder}, <nil>), want ErrClaimLost
--- FAIL: TestSucceededNotClaimable
```
Why: IT's override keeps the fence and `next_attempt_at`, so a worker that read the job while it was pending still matched on connector,
fence and the due clause. The same happened with a succeeded job whose fence equalled the candidate's.

## 2. Claim fence check

```diff
@@ -135,7 +135,6 @@
 	match := bson.D{
 		{Key: "connector", Value: c.Connector},
 		{Key: "status", Value: c.SeenStatus},
-		{Key: "fence", Value: c.SeenFence},
 	}
 	match = append(match, due)
```
Failure:
```
store_test.go:423: claim {... Connector:google SeenStatus:pending SeenFence:0} at 2026-11-05 09:00:02 +0000 UTC = ({... Attempt:1 Fence:2 WorkerID:intruder}, <nil>), want ErrClaimLost
--- FAIL: TestStaleCandidateAfterManualRetry
```
Why (ABA): a candidate read as `{pending, fence 0}` before the first round. The job then went claim → dead → IT manual retry, which put it
back to `pending` at fence 1. Without the fence check, the old candidate matched the newer round.

## 3. Finish fence check

```diff
@@ -146,7 +146,6 @@
 	match := bson.D{
 		{Key: "connector", Value: c.Connector},
 		{Key: "status", Value: StatusRunning},
-		{Key: "fence", Value: c.Fence},
 	}
 	return bson.D{{Key: "_id", Value: c.InstanceID}, {Key: "provisioning", Value: bson.D{{Key: "$elemMatch", Value: match}}}}
```
Failure:
```
store_test.go:316: stale Succeed = <nil>, want ErrFenceRejected
--- FAIL: TestStaleWorkerFenceRejected
```
Why: worker A's lease expired and worker B reclaimed the job (fence 2, still `running`). Without the fence check, A's stale `Succeed`
(fence 1) matched `running` and overwrote B's attempt.

## 4. Claim due condition

```diff
@@ -137,7 +137,7 @@
 		{Key: "status", Value: c.SeenStatus},
 		{Key: "fence", Value: c.SeenFence},
 	}
-	match = append(match, due)
+	_ = due
 	return bson.D{{Key: "_id", Value: c.InstanceID}, {Key: "provisioning", Value: bson.D{{Key: "$elemMatch", Value: match}}}}, true
```
(`_ = due` keeps the file compiling. Only the clause is dropped from the filter.)
Failures:
```
store_test.go:341: claim {... Connector:google SeenStatus:running SeenFence:1} at 2026-11-05 09:00:00 +0000 UTC = ({... Attempt:2 Fence:2 WorkerID:intruder}, <nil>), want ErrClaimLost
--- FAIL: TestLiveLeaseNotReclaimable
store_test.go:379: claim {... Connector:google SeenStatus:retry SeenFence:1} at 2026-11-05 09:00:01.999 +0000 UTC = ({... Attempt:2 Fence:2 WorkerID:intruder}, <nil>), want ErrClaimLost
--- FAIL: TestRetryNotDueNotClaimable
```
Why: without `lease_until < now`, a running job whose lease was still live was stolen. Without `next_attempt_at <= now`, a retry was
claimed 1 ms before its backoff ended.

## Restoration

After each run, `store.go` was copied back from the saved copy and its sha256 was compared with the original. All four comparisons
matched. The final passing run in `output.txt` was made after all four experiments, with every guard present.
