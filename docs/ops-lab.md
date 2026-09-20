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
