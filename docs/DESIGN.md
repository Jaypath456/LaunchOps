# LaunchOps design

LaunchOps separates workflow decisions from external side effects. Django validates roles, prerequisites, evidence and final sign-off; Go executes account-creation work against a local simulator. A slow or interrupted simulator call can be retried without keeping an API request open. Workers can restart independently and compete for work. The [spec](V2_SPEC.md) describes the contract, and the [README](../README.md) gives setup and recorded results.

## Aggregate and compare-and-set

A workflow document contains the hire's metadata, embedded steps, issues, append-only events and provisioning jobs. Embedding makes a business transition and its event a single atomic MongoDB write. The blueprint supplies step structure; documents retain the blueprint version. This is convenient for reading a workflow and evaluating cross-step rules, at the cost of document growth and contention on a shared aggregate.

The PostgreSQL implementation at tag `v1-postgres` used row locks. The current repository reads a document, builds a decision, then conditionally updates it using its `version`. A successful business mutation advances the version. A miss rebuilds the decision against a fresh snapshot; bounded contention returns a conflict. It uses `matched_count`, because deciding whether the predicate matched is the concurrency question. Business writes remain in [repository.py](../backend/onboarding/repository.py).

For step completion, the filter also includes `steps: {$elemMatch: {key, status: "open"}}`. `arrayFilters` only controls which array elements receive a positional update. If none matches, MongoDB can still match the document and apply an event `$push` or version `$inc`. The element predicate in the query prevents such a write from matching at all.

The protections overlap honestly: either the version guard or the step element guard alone prevents a double completion. The element guard protects the monotone open-to-done transition and the no-matching-element case; the version guard makes broader decisions, including rescheduling and profile proof, validate against a consistent business snapshot. Both stay. [Concurrency tests](../backend/onboarding/test_concurrency.py) show this with `test_version_guard_alone_gives_exactly_once`, `test_step_element_guard_alone_gives_exactly_once`, `test_negative_control_without_guards_duplicates_are_detected`, `test_stale_snapshot_without_version_guard_records_a_duplicate`, and `test_stale_profile_proof_is_rebuilt_from_the_new_start_date`. There is no separate concurrency evidence directory.

## Durable queue and shared ownership

Creating a workflow inserts its pending provisioning jobs in the same document. This removes a save-then-enqueue gap without needing a broker or cross-document transaction. Workers poll due jobs, choose candidates and attempt a claim. Claiming matches connector, observed status, observed fence and the due-time or expired-lease condition through `$elemMatch`; `arrayFilters` selects the job to update. Every transition is conditional, including completion, failure and IT interventions.

Django owns `version`, `steps` and `issues`, inserts jobs, and implements IT retry and override. Go changes fields within its provisioning element and appends only `provisioning.*` events. Job writes do not advance the business version. A succeeded job is terminal, so stale business snapshots cannot incorrectly pass the access gate. Completing access requires all jobs succeeded or overridden and records their results as evidence.

This ownership avoids unnecessary version contention between unrelated job and business transitions, but services still share storage, schema, event history and aggregate write contention. Ownership is a code convention backed by tests and a shared validator, rather than independent database credentials enforcing field boundaries. A larger system would give the provisioner its own store.

## Leases, fencing and attempts

A winning claim sets the lease owner and expiry, advances the fence and increments attempts. An expired running job can be reclaimed. Every finish requires running status and the worker's fence; a resumed stale worker's finish matches nothing and logs `fence_rejected`. Lease expiry permits recovery but does not stop the old process from running or calling the external system. The fence protects local job state; external idempotency protects the side effect.

The fence never resets. Attempts describe the current retry round, so IT can reset attempts on manual retry from dead while retaining the fence. Otherwise an old candidate or worker could become valid again when a job returns to pending. Startup requires the lease to exceed the call timeout plus a margin; calls and database operations have deadlines. Graceful shutdown stops new claims and drains in-flight work within a bound, leaving unfinished claims recoverable by lease expiry.

The [job-store guard-removal report](evidence/provisioner/guard-removal-results.md) maps status, claim fence, finish fence and due checks to failing tests. [Raw removals](evidence/provisioner/guard-removal-raw/), [commands](evidence/provisioner/COMMAND.txt), [environment](evidence/provisioner/ENV.txt) and [output](evidence/provisioner/output.txt) retain the observations, including the deliberately failing missing-URI check. These experiments isolate guards whose protections overlap in an ordinary concurrent claim.

## Idempotency and orphaned keys

Step completion stores `Idempotency-Key` as `completion.request_id`. An authorized replay of a completed step returns its stored completion without another event, even if a different key arrives. Permissions precede replay. This does not give every API operation an operation identifier.

A provisioning job's `<instance_id>:<connector>` key stays constant across attempts, leases and manual retries. The simulator atomically claims a unique request key, stores its body and intended account identifier, creates the account under a unique connector/username constraint, then stores the response. A completed request replays the stored response. A changed body under the same key is rejected, and an account created under another key is a permanent conflict.

Drop-after-commit deliberately closes the connection after the side effect and response record exist. The worker cannot infer whether the request committed from that transport failure. A retry with the same key replays the original external reference, rather than creating another account.

A crash can also leave a request marked processing before an account exists, or after account insertion but before response storage. A young processing record returns an in-progress conflict. Once stale, a same-body retry with a stored intended account identifier can take over using compare-and-set on processing state and claim counter. It inserts or finds that intended account, stores the response and marks the request done. Completion also checks state and claim, so a stalled original cannot overwrite the takeover. Legacy processing records without an intended identifier cannot be taken over safely.

The [simulator guard report](evidence/provisioner/c12-guard-removal.md) names the tests for takeover races, timeout checks and stale completion. Its [raw experiments](evidence/provisioner/c12/raw/), [commands](evidence/provisioner/c12/COMMAND.txt), [environment](evidence/provisioner/c12/ENV.txt) and [passing output](evidence/provisioner/c12/output.txt) distinguish restored-code behavior from intentional guard removal. The simulator demonstrates the required external behavior; a real provider would need its own verified semantics.

## Retry classification

A successful response must contain an external reference. In-progress conflicts, throttling, server errors, deadlines and incomplete transport responses are retryable. Other client errors, including an account conflict under another key, are permanent. Redirects are not followed. A malformed successful response is retryable because its side effect may already exist. See [connector classification](../provisioner/internal/connectors/connectors.go).

Retryable failures use full jitter under a capped exponential ceiling based on the current attempt. The attempt budget bounds retries; exhaustion or a permanent error leaves the job dead. The transport does not silently resend account requests outside that state machine. IT can retry a dead job or override eligible non-running jobs with a reason; the override records the manual resolution.

## Failure modes

| Crash or stall point | Recovery and remaining consequence |
|---|---|
| API stops before workflow insertion | No workflow or jobs exist; the caller can retry after inspecting state. |
| Workflow insertion commits before its response arrives | Embedded jobs remain claimable; the caller must reconcile the existing workflow. |
| Business update commits before its response arrives | The API reports an ambiguous write; step completion can replay the stored completion. |
| Reschedule response is ambiguous | There is no operation id; the API returns service unavailable and the caller must inspect current state. |
| Worker reads a candidate, then another actor changes it | Claim compare-and-set rejects the stale candidate. |
| Worker dies after claim, before calling the simulator | Lease expiry makes the running job reclaimable. |
| Worker stalls across lease expiry | Another claim advances the fence; the stale finish is rejected. External calls still require idempotency. |
| Simulator dies after request claim, before account insertion | A retry takes over the stale processing key and uses its intended account identifier. |
| Simulator dies after account insertion, before response storage | Takeover finds the existing account and finishes the response record. |
| Original simulator request resumes after takeover | State and claim guards reject its stale completion. |
| Simulator commits then drops the connection | Same-key retry replays its stored response. |
| Worker receives success then dies before recording it | Reclaim and same-key replay recover the original external reference. |
| Worker cannot determine whether a finish write committed | Stored succeeded state is terminal; otherwise lease recovery or retry reconciles the side effect. |
| Transient errors consume the retry budget | Job becomes dead, potentially with an account already present; IT investigates and retries or overrides. |
| Worker receives shutdown | It stops claiming and drains; unfinished work recovers after lease expiry. |
| MongoDB is unavailable | Readiness fails, calls time out, workers back off and resume when MongoDB returns; there is no database failover. |
| Processing request lacks an intended account identifier | Automatic takeover is refused; manual investigation is required. |

## What the chaos evidence establishes

The [README results](../README.md#results) quote each campaign's verifier counters and configuration directly. [Campaign-1](evidence/chaos/campaign-1/verify.json), [campaign-2](evidence/chaos/campaign-2/verify.json) and [campaign-3](evidence/chaos/campaign-3/verify.json) recorded terminal jobs, matching references for succeeded jobs, no account-conflict deaths, no orphan simulator accounts and exercised fault coverage. Their configurations and environments are retained alongside the outputs; the [campaign guide](evidence/chaos/README.md) explains collection and verification. The earlier [run-1](evidence/chaos/run-1/verify.json) passed data invariants but failed its required fence-rejection coverage, so it cannot establish that fault case.

A passing campaign does not mean all work succeeded. Each campaign's `dead_with_accounts` list reports jobs whose account exists despite a dead job state. Repeated ambiguous failures can consume the attempt budget after the side effect happened. The verifier lists each connector, username and reference; IT must reconcile and override these cases. The [README](../README.md#results) names the observed cases explicitly.

These are observations of worker crashes, stalls and injected simulator faults on a single host. They do not prove host-loss recovery, database availability, arbitrary network-partition safety, multi-host clock correctness, real-provider semantics, throughput, or browser behavior. The browser walkthrough remains manual.

## Limitations and larger scale

Refresh tokens cannot be revoked. Deleting a user does not invalidate already issued access tokens: they remain usable for the rest of the configured lifetime, defined in [JWT settings](../backend/lms_project/settings.py). Authorization uses token claims rather than reloading a deleted user. Offset pagination can repeat a row when workflows are inserted between pages. Reschedule has no operation id, so an ambiguous write returns service unavailable rather than claiming success.

Worker clocks drive leases and retry times on a single host; multi-host skew is unhandled. Polling adds latency and database load. Standalone MongoDB has no replica set, change streams or database failover. Embedded histories grow, aggregate writes contend, and shared-document field ownership couples service changes. Accounts, identity, payroll and devices are simulated locally.

At larger scale, an outbox committed with business state plus a durable queue would separate dispatch from execution, with deduplication still required. A replica set could support change streams and improve database resilience, with careful resume and reconciliation behavior. The provisioner could own its job store. Multi-tenancy would require tenant-scoped authorization, uniqueness, data isolation, quotas and operational boundaries throughout the API, job keys and external calls. These are future design changes, not current capabilities.
