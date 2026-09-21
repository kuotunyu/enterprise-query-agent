# Local operations lab

## Runtime contract

This opt-in application wraps the existing `Service`; QueryPlan, compiler, SQL
policy, facts, database schema and accepted research results are unchanged.
The original `enterprise_query.api` application and `/api/*` remain independent.
This is a single-process, trusted-operator experiment using synthetic data and a
deterministic mock provider. It makes no production SLA, tenant isolation or
real-model latency claim.

Launch with one worker and bind HTTP to host loopback:

```console
uv run uvicorn enterprise_query.ops:create_ops_app --factory --host 127.0.0.1 --port 8011 --workers 1
```

Use the isolated lab's runtime DB environment only, never bootstrap credentials,
old databases or real Olist data. The executor retains its MySQL 8.4, `eqa_v1`,
SELECT-only runtime identity and dataset checks. Do not mount an existing local
environment, cost ledger or research evidence into this application.

### Startup controls

| Environment | Default | Meaning |
|---|---|---|
| `EQA_OPS_PROVIDER_DELAY_SECONDS` | `1` | Fixed delay for each mock planning call; finite 0–60 seconds. |
| `EQA_OPS_PROVIDER_ERROR` | `0` | `1` fails each planning call after its fixed delay; only `0`/`1` accepted. |
| `EQA_ENABLE_PAID_API` | unset or `0` | Any other value refuses startup. |
| `EQA_PROVIDER` | unset or `mock` | Any other value refuses startup. |

The wrapper never constructs an OpenAI provider or cost ledger. Fault injection
comes only from startup configuration, not question parameters. A nonempty
`EQA_BOOTSTRAP_PASSWORD` refuses startup. Database connection configuration uses
the existing `EQA_DB_*` and `EQA_DATASET_ID` runtime fields.

### HTTP interfaces

| Method and path | Input | Result |
|---|---|---|
| `GET /health/live` | — | 200 with `live` and `epoch`; no DB access. |
| `GET /health/ready` | — | Live DB identity check; 200/503 with `ready` and `epoch`. Draining always returns 503. |
| `GET /ops/meta` | — | Epoch, mock mode, fault settings, admission/retention limits and drain state. No connection details. |
| `POST /ops/sessions` | No body required | `epoch`, `session_id`; creation is always explicit. |
| `POST /ops/ask` | `epoch` plus all existing `QuestionRequest` fields | `epoch`, `answer` (existing AnswerEnvelope), `timings`, `work_pending`. |
| `POST /ops/cancel/{session_id}` | `{"epoch":"..."}` | Cancel the session; `cancelled`, epoch and outstanding job count. |
| `POST /ops/drain` | No body required | Immediately mark draining; return actual `outstanding_jobs`. |
| `GET /ops/metrics` | — | Bounded cumulative outcome/rejection counters, timing summaries and current gauges. |

`/ops/ask` fields are `epoch`, `question`, `request_id`, `session_id`, `revision`
(default 1), `as_of_date` and optional `clarification`. Unknown fields are rejected.
Business outcomes, including provider/SQL failures and timeouts, retain HTTP 200
with `answer.status`; admission/protocol errors instead return
`{"error":"code","epoch":"..."}`:

- 409: `stale_epoch`, `request_conflict`.
- 404: `unknown_session` (also an expired session).
- 429: `overloaded`, `session_capacity`, `request_capacity`.
- 503: `draining`, `cancel_failed` (the session stays cancelled; the slot remains
  held until underlying work actually finishes, even if the cancellation failed).

There is no retry or automatic session recreation. A restart creates a new epoch,
loses volatile sessions and dedup state, and leaves any interrupted client outcome
unknown. The client must explicitly create a session in the new epoch; this is
not permission to automatically replay an interrupted query.

### Capacity and retention

At most **4 active jobs** are admitted, including jobs waiting for the serialized
provider, timed-out jobs whose real worker threads still run, and cancellation
cleanup. Excess new jobs are rejected immediately, with no admission queue.
Provider execution remains serialized by the existing Service lock. The existing
per-task limits remain: 3 planning calls, 3 business SELECTs, 2 clarifications,
60 seconds active processing and 5 seconds per SQL execution.

An identical request ID/fingerprint coalesces with the existing response without
another slot or provider/SQL execution. Reusing an ID for any different request
(including a different session) is a conflict. Draining rejects new sessions and
new requests but permits retained duplicate responses and cancellation.

At most **500 sessions**, each with at most **32 retained request IDs**, are kept.
A full request cache rejects new IDs; it does not evict entries promised for
deduplication. Idle sessions and their requests are retained for **30 minutes**
from creation or their latest *actual job completion*, whichever is later.
Duplicate reads do not extend retention. Cleanup runs on session creation,
session validation and metric reads. It removes corresponding Service tasks,
request futures and fingerprints together. A session with underlying work or a
pending cancellation is never reclaimed, even past its nominal TTL.

Cancelling an HTTP await (for example a disconnected client) does not cancel the
supervisor or release capacity. A business timeout may return while its provider,
DB identity check or SQL worker still runs. `work_pending=true` explicitly marks
that distinction. Cancellation cannot forcibly kill a Python provider thread.
The supervisor releases the slot only after the real workers and any cancellation
operation finish. The executor also waits for its own already-running SQL
deadline callback before its worker completes; cancelling a timer alone does not
stop a callback that has already opened a cancellation connection. This wait runs
outside the executor lock, so callback cleanup cannot deadlock on that lock.
Graceful application shutdown drains and waits for this work;
forced process termination still has the restart/unknown-outcome boundary above.

Readiness has a **1-second response deadline**, checks the live DB identity, and
shares at most one outstanding check. A check that outlives its HTTP deadline is
kept and coalesced until the real thread ends; repeated outage probes cannot
spawn an unbounded pile of checks. Its check is independent of admission slots;
liveness remains responsive. A successful check that finishes after drain cannot
restore readiness.

### Timing and measurement boundary

The clock is monotonic (`perf_counter` for precise elapsed measurement). `/ops/ask`
reports seconds for `queue_seconds` (global admission guard, session and
serialized-provider waits),
`provider_seconds`, `sql_seconds` (identity checks plus business SQL), and
`total_seconds` (admission to actual completion). These are wall durations,
including thread scheduling overhead, not GPU or MySQL server-only timings.
Cancellation cleanup contributes to total time. A response with pending work
contains a partial timing snapshot; its total is elapsed time so far. Fetching
the same request later returns the same answer with updated timing/completion
metadata and performs no new business work.

`/ops/metrics` holds fixed-shape cumulative `count`/`sum`/`max` per timing and
bounded status/code counters. Completed timing/outcome summaries are recorded
once per admitted job **after underlying work finishes**; `active_jobs` exposes
unfinished work separately. Provider/SQL failure paths are included. Immediate
admission/protocol rejections are counted separately and perform no pipeline
work. It stores no per-request metric labels, questions, SQL, values, credentials,
provider traces or connection identities. The ordinary `answer` still contains
its existing business evidence; measurement reports should use the metrics and
timing fields, not copy answer tables or SQL values.

For each load cell, start a fresh epoch and use a bounded session pool with fresh
sessions as task budgets are exhausted. Do not confuse the 500-session/32-request
retention caps or 3-SELECT task budget with the 4-job admission ceiling. Record
failed outcomes; wait for `active_jobs=0` before collecting final metrics, or
explicitly report lingering work. There is no automatic reset endpoint.

### Offline verification

```console
uv run pytest tests/test_ops.py -q
uv run pytest -m "not integration and not ui" -q
```

The runtime tests use the real Service and replace only external provider/DB
work with deterministic, event-controlled workers. They cover coalescing,
capacity, retention, epoch validation, late completion, disconnect/cancel/drain,
live readiness during a slow cancellation and timing on failures. They do not
claim a running MySQL/container experiment or any paid-provider measurements.

## Isolated container deployment

`scripts/ops_deploy.py` owns only uniquely named `eqaops-*` Compose projects.
It never uses the original `compose.yaml`. Each deployment creates a fresh
MySQL 8.4 volume, credentials and ignored `.local/ops/<stack>/` directory;
existing resources or directories with the requested name are rejected.
MySQL listens privately on 3307 without any published host port. Bootstrap and
integration one-shot containers share MySQL's network namespace, preserving
the existing initializer's loopback-only/non-3306 guard. The app uses
`mysql:3307`; only its HTTP port is published, on host `127.0.0.1`.
Both image variants run uvicorn as PID 1, with one worker. The container's
`0.0.0.0:8011` listener is behind that loopback-only host mapping.

Commit the reviewed deployment source before building, then use the paths
printed by each command (replace the example names/receipt paths):

```console
uv run --locked python scripts/ops_deploy.py build
uv run --locked python scripts/ops_deploy.py deploy eqaops-run-a --port 18011 --image-receipt .local/ops/builds/<runtime-receipt>.json
uv run --locked python scripts/ops_deploy.py verify eqaops-run-a
uv run --locked python scripts/ops_deploy.py status eqaops-run-a
uv run --locked python scripts/ops_deploy.py deploy eqaops-run-b --port 18012 --image-receipt .local/ops/builds/<same-runtime-receipt>.json
uv run --locked python scripts/ops_deploy.py verify eqaops-run-b
```

`deploy` starts a healthy MySQL, invokes the unchanged synthetic initializer,
checks the committed `base.json` against its generator and the independent
Python/real MySQL hand oracle, then starts the app and checks live readiness.
`verify` additionally runs the existing integration-marked tests in a one-shot
container with reader credentials. `EQA_MUTATION` stays unset: destructive
3308-only tests are skipped. The old `/api/*` HTTP flow and browser suite need
the independent original application; they are outside this stack and are not
counted as operations endpoint coverage. This is synthetic development
verification, never a locked research evaluation.

Every command writes a fresh, exclusive receipt, including failed attempts.
Credentials are generated exclusively in three separate env files, are never
printed, and are not image build inputs. These local files are sensitive and
must not be published. Unix mode 0600 is requested; on Windows their access
inherits workspace ACLs, so this remains a single trusted operator setup.
The app receives only reader credentials. Its isolated `state` mount survives
image replacement, but sessions/dedup are deliberately volatile and restart
with a new epoch; no requests are replayed and cost locks are never reset.

### Rebuild and provenance boundary

The build context is a separate directory assembled from Git-tracked allowlisted source,
tests, schema/catalog and public synthetic fixture files. It excludes ignored
files, local state, old ledgers, real data and research reports. Dockerfile
COPY and `.dockerignore` provide another boundary; use the CLI rather than
an unrestricted manual context. Receipts include Git SHA and honest dirty
state, each context file's hash, context-manifest/lock hashes, exact image IDs and
resolved repository digests. Builds resolve the Python 3.12 and uv 0.11.18
image tags once and pass immutable digests to Docker; deployment uses the
resulting app/MySQL image IDs with pull disabled. The existing local
`mysql:8.4` image is recorded, not silently upgraded. `uv sync --locked
--no-install-project` installs the committed dependency lock; source is loaded
through explicit `PYTHONPATH`, avoiding an unpinned isolated build backend.
The installed lock hash and actual runtime DB identity are checked by the
container oracle. These records do not promise bit-identical image rebuilding
across platforms or availability of deleted image caches. Keep exact images
and their receipts for rollback; no registry publication occurs.

### Drain, replacement and explicit rollback

```console
uv run --locked python scripts/ops_deploy.py build --target unready
uv run --locked python scripts/ops_deploy.py update eqaops-run-a --image-receipt .local/ops/builds/<unready-receipt>.json
uv run --locked python scripts/ops_deploy.py rollback eqaops-run-a --image-receipt .local/ops/builds/<previous-runtime-receipt>.json
uv run --locked python scripts/ops_deploy.py stop eqaops-run-a
uv run --locked python scripts/ops_deploy.py remove eqaops-run-a
```

Replacement first drains and waits up to 70 seconds for actual active work to
reach zero; a timeout refuses forced replacement. The app then restarts with
the requested exact lab image while DB/state remain. The deliberate `unready`
image overrides only its DB port to 1, leaving liveness up but readiness false.
Its update is expected to produce a **failed receipt**; it remains in that
state until an explicit rollback, with no automatic retry or replay.
Rollback is the same bounded process targeting the prior receipt's exact ID.
`stop` preserves everything. `remove` requires the saved stack receipt and
unchanged Compose snapshot, removes only its containers/network, and retains
the database volume and all evidence. Neither command invokes global prune,
deletes volumes/images, or touches unrelated Docker projects.

The database stays on an internal private network. The app also joins its own ingress bridge so Docker Desktop can realize its host-loopback HTTP publication; no other service joins ingress. Receipts check actual running port bindings, not only requested configuration. The first internal-only deployment failed host readiness despite passing its database oracle; that failed receipt and volume remain preserved.

## Independent measurements

`scripts/ops_measure.py` is a separate host HTTP client. It never imports the
Service, planner or compiler. Its hand oracle checks July 2018 delivered GMV
242 BRL, 3 orders and AOV 242/3 from the public synthetic fixture, including
population and date boundaries. This is engineering verification with a
deterministic mock, not a model evaluation or production SLA.

Run only one operator/measurement process against a stack. Every invocation
uses a fresh `.local/ops/runs/<UTC-action-UUID>/` directory. Its manifest records
the exact runtime image/build receipt/source SHA separately from the host
runner HEAD, dirty status and file hash. Host runner source snapshots, raw
JSONL events, failed responses and failed deployment receipts remain local.
No ask retries or automatic replay occur, including after HTTP timeouts.
An interrupted/unknown request remains unknown. `summarize` writes a new file
each time and can recover complete JSONL records from a truncated final line.

Full experiments, **only after task and whole-branch review**, use:

```console
uv run --locked python scripts/ops_measure.py load eqaops-task2-c --runtime-receipt .local/ops/builds/20260920T131651-build-runtime-395434de.json
uv run --locked python scripts/ops_measure.py faults eqaops-task2-c --runtime-receipt .local/ops/builds/20260920T131651-build-runtime-395434de.json --bad-receipt .local/ops/builds/20260920T131813-build-unready-63089bbe.json
uv run --locked python scripts/ops_measure.py soak eqaops-task2-c --runtime-receipt .local/ops/builds/20260920T131651-build-runtime-395434de.json
uv run --locked python scripts/ops_measure.py summarize .local/ops/runs/<run-id>
```

These receipts belong to the existing local C stack. On another machine,
substitute that machine's validated stack and exact successful image receipts.
Changing only the host harness does not require rebuilding the measured image.
Do not change the runtime/database/fixture/config during a full experiment.

`load` defaults to 0.25, 0.5, 1 and 2 requests/second, each for 300 seconds,
with 3 independent repeats: 60 minutes of offered-traffic windows plus setup
and tail time. Every cell drains actual work, recreates the identical app image
with a fresh epoch and fixed 1-second mock delay, and precreates at most 300
sessions. The largest cell offers 600 asks: revision 1 across 300 sessions,
then revision 2 across them, 150 seconds later. At most 2 asks/session avoids
the 3-provider/3-SQL task budgets and 500-session cap. Setup traffic is separate.
A still-active or uncertain earlier request prevents that session's second
send and is recorded as a generator failure; it is never superseded or delayed.

Arrival deadlines are absolute and independent of response completion.
Every offered event records schedule lag. Arrivals over 1 second late become
explicit generator failures instead of a catch-up burst. Summaries distinguish
offered, sent, terminal client observations, generator failures, unknown sent
requests, HTTP codes, business outcomes and oracle correctness. Client p50/p95
use linear interpolation, separately for all/correct/failed observations;
queue/provider/SQL/total timing distributions retain partial `work_pending`
flags in raw responses. Final runtime aggregate timings are recorded after
drain reaches zero. The full window ends before client/server tail accounting.
`completed` means the experiment finished, not that every request succeeded.

The sampler runs outside the scheduler's event loop via a worker thread.
It verifies uvicorn at container PID 1, reads that process's `/proc` CPU ticks
and VmRSS, and reports CPU percent with one core = 100%. CPU is null on the
first sample or process identity change. RSS is process RSS, not Docker's
container-memory statistic. `Threads_connected` includes the sampler's own
short-lived reader connection. Readiness, epoch, sessions, retained requests
and active jobs accompany samples. Missing/failed samples are null plus error
types, never zero; sample duration and scheduling gaps are retained. Sampling
adds DB/CPU overhead. Tail drain intentionally makes readiness false and is
identified by `metrics.draining`; final normal restoration creates a new epoch.

`faults` defaults to 3 independent repeats of each of five scenarios: database
stop/recovery, slow provider (5 seconds) with HTTP session cancellation,
failing provider, in-flight app kill, and unready-image update/rollback.
Each repetition begins at a fresh epoch and checks the oracle before/after.
Provider scenarios use saved nonsecret Compose overrides, never arbitrary
fault/SQL HTTP routes. Fault timestamps, HTTP observations, recovery readiness
and oracle recovery durations are recorded; old epochs must be rejected after
app replacement. DB-only restart keeps its epoch, explicitly marked as such.
Restoration is attempted in `finally` after injection errors. An OS/process
kill of the harness itself cannot guarantee cleanup: inspect raw evidence and
use the explicit deployment rollback/start commands before resuming. Failed
candidates and receipts are retained, and requests are never replayed.

App-kill recovery explicitly recreates only the app with the same immutable
image, then verifies a new running container, readiness and a new epoch before
recording recovery success. Its separate `measurement-kill-recovery` receipt
retains pre/post resources and redacted Compose stdout plus stderr, including
successful-command stderr (opt-in; normal JSON stdout consumers are unchanged).
The completed campaign's first kill recovery had returned from generic Compose
`up` but left the app exited; the original command streams were unavailable,
and a later identical recovery succeeded. The Docker/Compose trigger remains
unresolved. Explicit replacement is a narrow recovery mitigation, not a proven
fix to an upstream cause. It changes the measured recovery procedure and must
be reported with the new host runner revision. Runtime image, database, load
arrival/timing/oracle semantics are unchanged; existing load evidence remains
applicable. This never replays the interrupted request.

HTTP cancellation here observes deterministic provider delay, **not** long SQL
cancellation. Separate real-MySQL evidence is the existing
`tests/test_executor_layers.py::test_explicit_cancel_stops_real_query` and
`test_default_five_second_deadline_and_no_residual`, recorded by Task 2's
integration verification. Ordinary compiled synthetic queries are too short
to make an HTTP-triggered long-SQL cleanup claim.

`soak` defaults to a genuine 86,400-second window with one newly created
synthetic session every 60 seconds (1,440 offered events). The runner waits
through the final minute before marking the window complete and captures
final cache/resource/drain evidence. Sampling records gaps that may indicate
host sleep, suspension or scheduler delays; wall-clock duration alone cannot
establish continuous operation. Review missing arrivals, readiness, gaps and
epoch changes before claiming continuity. The abbreviated diagnostics below
are never 24-hour evidence. Full load/fault/soak evidence remains pending until
the controller runs the reviewed package for the prescribed durations.

```console
uv run --locked python scripts/ops_measure.py smoke eqaops-task2-c --runtime-receipt .local/ops/builds/20260920T131651-build-runtime-395434de.json
uv run --locked python scripts/ops_measure.py load eqaops-task2-c --runtime-receipt .local/ops/builds/20260920T131651-build-runtime-395434de.json --diagnostic --rates 2 --duration 6 --repeats 1
uv run --locked python scripts/ops_measure.py faults eqaops-task2-c --runtime-receipt .local/ops/builds/20260920T131651-build-runtime-395434de.json --bad-receipt .local/ops/builds/20260920T131813-build-unready-63089bbe.json --diagnostic --repeats 1
uv run --locked python scripts/ops_measure.py soak eqaops-task2-c --runtime-receipt .local/ops/builds/20260920T131651-build-runtime-395434de.json --diagnostic --duration 8 --interval 2
```
