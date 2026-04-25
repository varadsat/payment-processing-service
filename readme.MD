# Payment Reconciliation Service

A backend service that ingests payment lifecycle events, projects them into
transaction state, and exposes APIs for an operations team to query
transactions and find reconciliation discrepancies.

Built with **Python 3.12, FastAPI, SQLAlchemy 2.0, Postgres 16, and Alembic**.

- **Live demo:** `https://payment-processing-service.fly.dev`
- **Interactive API docs:** `https://payment-processing-service.fly.dev/docs`
- **Postman collection:** [`examples/postman/payment-processing-svc.postman_collection.json`](examples/postman/payment-processing-svc.postman_collection.json)
- **Screen recording:** `https://drive.google.com/drive/folders/1rIZrEvP08X1_CKP0_UX2dFW3x_Jy5-gX?usp=sharing`

---

## Architecture overview

The service sits between upstream payment systems and an operations team. It
receives events describing the lifecycle of payments — `payment_initiated`,
`payment_processed`, `payment_failed`, `settled` — and answers four kinds of
questions:

- _What's the current state of this transaction?_ (and how did it get there)
- _What's happening across a merchant or time window?_ (filtered listing)
- _What are the totals?_ (grouped aggregations)
- _What's broken?_ (discrepancies between payment and settlement state)

### Domain model — events vs. transactions

The core design decision is separating two concepts that are often conflated:

**Events** are immutable, append-only facts. They are never updated or
deleted. Each event has a unique `event_id` which acts as the idempotency
key, enforced by a unique constraint at the database layer.

**Transactions** are a _projection_ — a derived view that answers "what is
the current state of this transaction?" The projection is updated by the
same code that ingests an event, inside the same database transaction. There
is no separate worker, no consistency window, no eventual catch-up.

This is a lightweight event-sourcing pattern. The event log is the source of
truth; the projection makes reads fast.

```
┌─────────────┐       ┌──────────────┐       ┌──────────────────┐
│  POST       │──────▶│  Ingestion   │──────▶│  events          │
│  /events    │       │  service     │       │  (append-only,   │
└─────────────┘       │              │       │   UNIQUE event_id│
                      │  (single DB  │       └──────────────────┘
                      │  transaction │                │
                      │  + row lock) │                ▼
                      │              │       ┌──────────────────┐
                      │              │──────▶│  transactions    │
                      └──────────────┘       │  (projection +   │
                                             │   conflict flag) │
                                             └──────────────────┘
                                                      ▲
┌─────────────┐                                       │
│  GET        │───────────────────────────────────────┘
│  /transact. │
│  /reconcile │
└─────────────┘
```

### Schema

Three tables, each with indexes that match the queries the API actually
makes. Postgres-native types are used where they make the schema clearer
(`TIMESTAMPTZ`, `JSONB`, enums).

**Enums**

```sql
CREATE TYPE payment_status    AS ENUM ('initiated', 'processed', 'failed');
CREATE TYPE settlement_status AS ENUM ('pending', 'settled', 'not_applicable');
CREATE TYPE conflict_reason   AS ENUM (
  'stuck_settlement', 'settled_after_fail',
  'conflicting_transition', 'duplicate_detected'
);
```

**`merchants`**

| Column        | Type          |
| ------------- | ------------- |
| `merchant_id` | `TEXT` PK     |
| `name`        | `TEXT`        |
| `created_at`  | `TIMESTAMPTZ` |

**`transactions`** — the projection

| Column              | Type                | Notes                      |
| ------------------- | ------------------- | -------------------------- |
| `transaction_id`    | `TEXT` PK           |                            |
| `merchant_id`       | `TEXT`              | FK → `merchants`           |
| `amount`            | `NUMERIC(18,2)`     |                            |
| `currency`          | `TEXT`              |                            |
| `payment_status`    | `payment_status`    |                            |
| `settlement_status` | `settlement_status` |                            |
| `first_event_at`    | `TIMESTAMPTZ`       |                            |
| `last_event_at`     | `TIMESTAMPTZ`       |                            |
| `has_conflict`      | `BOOLEAN`           | precomputed at ingest time |
| `conflict_reason`   | `conflict_reason`   | nullable                   |

Indexes: `(merchant_id, last_event_at DESC)`, `(payment_status,
settlement_status)`, `(last_event_at DESC)`, `(has_conflict)`.

**`events`** — append-only log

| Column            | Type            | Notes                              |
| ----------------- | --------------- | ---------------------------------- |
| `event_id`        | `TEXT` PK       | idempotency key (unique)           |
| `transaction_id`  | `TEXT`          | FK → `transactions`                |
| `merchant_id`     | `TEXT`          | FK → `merchants`                   |
| `event_type`      | `TEXT`          | `payment_initiated`, etc.          |
| `amount`          | `NUMERIC(18,2)` |                                    |
| `currency`        | `TEXT`          |                                    |
| `event_timestamp` | `TIMESTAMPTZ`   | when the event happened (payload)  |
| `received_at`     | `TIMESTAMPTZ`   | when we ingested it (server clock) |
| `raw_payload`     | `JSONB`         | full original payload, for audit   |

Index: `(transaction_id, event_timestamp)` — powers the event-history query.

### State machine

```
Payment:   None → initiated → processed ─┐
                            └→ failed    ┤
                                         ▼
Settlement: pending ───────────────→ settled
              └──────────────────→ (stays pending if failed)
```

Two separate state fields, not one. A single `status` field couldn't express
"payment processed but settlement still pending" — which is exactly the
discrepancy the service exists to detect.

### Discrepancy rules

All four are evaluated at ingest time and stored in `has_conflict` /
`conflict_reason`, so the discrepancies endpoint is a single indexed SELECT.

1. **`stuck_settlement`** — payment is `processed` but settlement is still
   `pending` more than 24 hours after the last event.
2. **`settled_after_fail`** — payment is `failed` but settlement is
   `settled`. An impossible state; usually indicates a bug or fraud upstream.
3. **`conflicting_transition`** — an event tries to move state in an invalid
   direction (e.g. a `settled` event for a payment already marked `failed`,
   or a terminal status transitioning backward).
4. **`duplicate_detected`** — a duplicate `event_id` was received after the
   original. Logged for audit; does not mutate state.

### Ingestion flow

When `POST /events` receives a payload, the following happens inside one
database transaction:

1. **Validate** the payload (Pydantic). Reject malformed input with 422.
2. **Upsert merchant** via `INSERT ... ON CONFLICT (merchant_id) DO NOTHING`.
3. **Insert event** via `INSERT ... ON CONFLICT (event_id) DO NOTHING
RETURNING event_id`. If no row is returned, the event is a duplicate —
   short-circuit and respond with `{"status": "duplicate"}`.
4. **Lock the transaction projection row** with `SELECT ... FOR UPDATE`
   (creating the row if it doesn't exist). This serializes concurrent
   writers for the same transaction.
5. **Apply the state transition** via a pure function. If the transition is
   invalid, the event is still recorded but the transaction is flagged with
   `conflicting_transition`.
6. **Recompute `has_conflict` and `conflict_reason`** based on the new state.
7. **Commit.**

The pure transition function `apply_transition(current_state, event) →
(new_state, conflict_reason | None)` is the heart of the service and is
covered by unit tests for every transition pair.

### Idempotency

Idempotency is enforced by the database, not by application code. The unique
constraint on `events.event_id` is the final authority. Combined with `INSERT
... ON CONFLICT DO NOTHING RETURNING`, a duplicate submission becomes a
single round trip that mutates nothing and returns a clear "duplicate"
response — no exception handling, no race conditions, no half-applied state.

### Project layout

```
app/
├── api/                  # FastAPI routers, one per resource
│   ├── events.py
│   ├── transactions.py
│   └── reconciliation.py
├── db/
│   ├── engine.py         # engine, session, get_session() dependency
│   └── models.py         # SQLAlchemy models
├── schemas/              # Pydantic request/response models
│   ├── events.py
│   ├── transactions.py
│   └── reconciliation.py
├── services/
│   └── events.py         # ingestion + state machine
├── config.py             # pydantic-settings
└── main.py               # create_app()
alembic/                  # migrations
scripts/
└── seed.py               # generates + loads sample data
tests/
├── conftest.py
└── test_*.py
```

---

## Setup instructions for local development

### Prerequisites

- Docker + Docker Compose (the simplest path)
- Or, for native development: Python 3.12 and [uv](https://docs.astral.sh/uv/)

### Option A — Docker Compose (one command)

The compose file brings up Postgres 16 and the FastAPI app together.

```bash
cp .env.example .env             # only used by the app service if you override
docker compose up --build
```

Migrations run automatically on container start (`alembic upgrade head` is
chained before `uvicorn` in the compose `command`). To optionally load sample
data, in another terminal:

```bash
docker compose exec app .venv/bin/python -m scripts.seed   # if scripts/seed.py is present
```

The API is served at `http://localhost:8000` and Swagger UI at
`http://localhost:8000/docs`.

### Option B — Native (uv) with Postgres in Docker

Run only Postgres in Docker, run the app on the host:

```bash
docker compose up -d postgres
cp .env.example .env
# Make sure DATABASE_URL points at localhost:5432
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

### Environment variables

Only one is required:

| Variable       | Example                                                          |
| -------------- | ---------------------------------------------------------------- |
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/payments` |

Note the driver prefix is `postgresql+psycopg` (psycopg v3), not
`postgresql+psycopg2`.

### Running tests

```bash
uv run pytest
```

Tests hit a real Postgres — point `DATABASE_URL` at a disposable database
before running them. The compose Postgres works fine.

---

## API documentation

Interactive OpenAPI docs are available at `/docs` (Swagger UI) and `/redoc`
on any running instance. The summaries below cover the contract; `/docs` is
the canonical reference.

### Health

| Method | Path      | Purpose                                                 |
| ------ | --------- | ------------------------------------------------------- |
| GET    | `/health` | Liveness check; runs `SELECT 1` to verify DB reachable. |

### Events

#### `POST /events`

Ingest a single payment lifecycle event. Idempotent on `event_id`.

Request body (`EventIn`):

```json
{
  "event_id": "b768e3a7-9eb3-4603-b21c-a54cc95661bc",
  "event_type": "payment_initiated",
  "transaction_id": "2f86e94c-239c-4302-9874-75f28e3474ee",
  "merchant_id": "merchant_2",
  "merchant_name": "FreshBasket",
  "amount": 15248.29,
  "currency": "INR",
  "timestamp": "2026-01-08T12:11:58.085567+00:00"
}
```

Validation rules:

- `event_type` must be one of `payment_initiated`, `payment_processed`,
  `payment_failed`, `settled`.
- `amount` must be greater than zero.
- `currency` must be exactly three uppercase letters (e.g. `INR`, `USD`).
- `timestamp` must be timezone-aware (naive datetimes are rejected).

Responses:

| Status | When                                                          |
| ------ | ------------------------------------------------------------- |
| 201    | New event accepted, projection updated.                       |
| 200    | Event was a duplicate, or accepted but flagged as a conflict. |
| 422    | Payload failed validation.                                    |

Response body (`IngestResult`):

```json
{
  "status": "created", // "created" | "duplicate" | "flagged"
  "transaction_id": "2f86...",
  "conflict_reason": null // or one of the four conflict_reason values
}
```

#### `POST /events/bulk`

Accepts a JSON array of `EventIn` and ingests each in its own database
transaction, so a single bad event cannot fail the rest of the batch.

Returns a `list[IngestResult]` of the same length and order as the input.

### Transactions

#### `GET /transactions`

List transactions, with filtering, sorting, and pagination — all pushed into
SQL.

Query parameters:

| Name                | Type   | Default          | Notes                                           |
| ------------------- | ------ | ---------------- | ----------------------------------------------- |
| `merchant_id`       | string | —                | Exact match.                                    |
| `payment_status`    | string | —                | `initiated` / `processed` / `failed`.           |
| `settlement_status` | string | —                | `pending` / `settled` / `not_applicable`.       |
| `from`              | date   | —                | Inclusive lower bound on `last_event_at`.       |
| `to`                | date   | —                | Inclusive upper bound on `last_event_at`.       |
| `sort`              | string | `-last_event_at` | `last_event_at` or `amount`; `-` prefix = DESC. |
| `limit`             | int    | 50               | 1–200.                                          |
| `offset`            | int    | 0                | ≥ 0.                                            |

Response (`TransactionListResponse`):

```json
{
  "items": [
    /* TransactionOut */
  ],
  "total": 1234,
  "limit": 50,
  "offset": 0
}
```

#### `GET /transactions/{transaction_id}`

Returns the transaction projection along with the merchant and the full,
chronologically-ordered event history.

Responses: `200` with the detail body, or `404` if the id is unknown.

### Reconciliation

#### `GET /reconciliation/summary`

Grouped aggregations — counts and `SUM(amount)` — produced by a single SQL
`GROUP BY` query.

Query parameters:

| Name       | Type   | Notes                                                     |
| ---------- | ------ | --------------------------------------------------------- |
| `group_by` | string | **Required.** CSV of any of `merchant`, `date`, `status`. |
| `from`     | date   | Optional lower bound on `last_event_at`.                  |
| `to`       | date   | Optional upper bound on `last_event_at`.                  |

`currency` is always part of the grouping key — totals are never summed
across currencies.

Response shape:

```json
{
  "group_by": ["merchant", "status"],
  "groups": [
    {
      "keys": {
        "merchant_id": "merchant_2",
        "payment_status": "processed",
        "settlement_status": "settled"
      },
      "count": 712,
      "total_amount": "9241830.55",
      "currency": "INR"
    }
  ]
}
```

#### `GET /reconciliation/discrepancies`

Lists transactions where `has_conflict = TRUE`. A single indexed SELECT —
because `has_conflict` and `conflict_reason` are precomputed at ingest time.

Query parameters:

| Name     | Type   | Default | Notes                                                                                            |
| -------- | ------ | ------- | ------------------------------------------------------------------------------------------------ |
| `reason` | string | —       | One of `stuck_settlement`, `settled_after_fail`, `conflicting_transition`, `duplicate_detected`. |
| `limit`  | int    | 50      | 1–200.                                                                                           |
| `offset` | int    | 0       | ≥ 0.                                                                                             |

Each item includes an `age_hours` field computed in SQL as
`EXTRACT(EPOCH FROM now() - last_event_at) / 3600`.

---

## Deployment details

The deployed copy runs on **Fly.io** (Singapore region) backed by a **Neon**
Postgres instance.

### Files involved

- [`Dockerfile`](Dockerfile) — single-stage Python 3.12 image; installs deps
  with `uv sync --frozen --no-dev`, runs `uvicorn` on port 8000.
- [`fly.toml`](fly.toml) — Fly app config. `release_command` runs
  `alembic upgrade head` on every deploy, so migrations are applied before
  the new version takes traffic. `auto_stop_machines = 'stop'` lets the
  free-tier machine scale to zero when idle.
- [`docker-compose.yml`](docker-compose.yml) — local Postgres + app, used
  for development only.

### First-time deploy

```bash
fly launch --no-deploy --copy-config        # creates the app from fly.toml
fly secrets set DATABASE_URL='postgresql+psycopg://USER:PASS@HOST/DB?sslmode=require'
fly deploy
```

The `release_command` will run `alembic upgrade head` against the Neon
database. Health is checked at `/health`.

### Seeding production data

After the first deploy, load sample data via the bulk-ingest endpoint or by
running the seed script in a one-off machine:

```bash
fly ssh console -C ".venv/bin/python -m scripts.seed"
```

### Redeploy

```bash
fly deploy
```

### Production sanity checks

- `GET /health` returns `{"status":"ok"}`.
- `GET /docs` loads.
- `GET /transactions?limit=5` returns rows.
- `GET /reconciliation/discrepancies?limit=5` returns at least one of each
  conflict type after seeding.

---

## Assumptions and tradeoffs

This section is the honest version of "why is the code shaped this way."
Each decision has a reason and a known tradeoff.

### Events are assumed to arrive in order

The state machine in `apply_transition` decides the next state by reading the
_currently-stored_ `payment_status` from the projection — it does not compare
the incoming event's `timestamp` against `last_event_at`. So the service
implicitly assumes events for a given `transaction_id` arrive in the same
order they were emitted upstream (which is what you get from a single Kafka
partition keyed by `transaction_id`, or any single-producer source).

If a `payment_processed` event arrives before its corresponding
`payment_initiated` for the same transaction, the service will treat it as a
`conflicting_transition` rather than recognising it as a late-arriving
in-order event. With more time I would make ingestion order-tolerant by
recomputing the transaction projection on every insert: load all events for
the `transaction_id` ordered by `event_timestamp` and fold the state machine
over them, so the projection becomes a pure function of the event log and
arrival order stops mattering.

### Amounts are assumed to tally across events for a transaction

All events for the same `transaction_id` are assumed to carry the same
`amount` and `currency`. The projection takes its `amount` from the first
event and is not re-validated on subsequent events, so a mismatch is
silently accepted today. With more time, I would add an `amount_mismatch`
conflict reason and check this at ingest, the same way the other four
conflicts are flagged.

### Why Postgres, not SQLite

SQLite would have worked technically — at 10k events with a single-node
service, it's fast enough and simpler locally. The reason for Postgres is
deployment realism. Free-tier PaaS hosts have ephemeral filesystems, so
SQLite needs a specific platform with a mounted volume to persist data
across restarts. Postgres on Neon's free tier is a connection string and
nothing else; the deployment story stays simple.

The tradeoff: an extra service in `docker-compose.yml` for local dev. Worth
it for a clean deploy.

### Why a projection alongside the event log, not pure event sourcing

Pure event sourcing — recompute state from events on every read — is elegant
but slow at any non-trivial volume and impossible to index for aggregations
like "sum of settled amounts per merchant per day." A projection makes the
read path a single indexed SELECT.

Keeping both means every question reviewers might ask has a one-query
answer: the projection answers "what's the current state," the event log
answers "how did it get there." If the projection ever drifts, it can be
rebuilt deterministically by replaying the events.

The tradeoff: the projection is technically derived data and could be
inconsistent if a future maintainer forgets to update it inside the event
ingestion transaction. Mitigated by keeping all writes through a single
service method.

### Why precompute `has_conflict` at ingest time

The discrepancies endpoint is the primary reason this service exists for an
ops team. They look at it constantly. Computing the discrepancy logic at
read time would mean every discrepancies request runs a series of conditions
across millions of rows; precomputing it means the endpoint is

```sql
SELECT ... FROM transactions WHERE has_conflict = TRUE;
```

against an indexed boolean. The work moves from "every read" to "every
write," and reads happen far more often than writes.

The tradeoff: if the discrepancy rules change, existing rows need a
backfill. Acceptable cost — the rules don't change often, and a one-time
`UPDATE ... SET has_conflict = ...` is straightforward.

### Why two separate status fields

A single `status` field can't express "payment is processed but the merchant
hasn't been paid yet" — which is exactly the most important state in
reconciliation. Payment processing and settlement are physically different
systems that succeed and fail independently. Modeling them as two fields
lets the schema match the real world, makes the discrepancy rules
expressible as plain SQL, and avoids a combinatorial explosion of status
values.

### Why offset pagination, not cursor pagination

Cursor pagination is more robust under concurrent writes and is the right
choice at scale. At 10k rows for a single ops team's dashboard, the
robustness benefit doesn't materialize — and offset pagination with `total`
in the response is the format ops dashboards actually want, since it lets
them show "page 3 of 17" or jump to the end.

The tradeoff: at high write rates, offset pagination can repeat or skip
rows when new events land mid-pagination. If this service grew to that
scale, switching to cursor pagination would be the next change.

### Why `SELECT ... FOR UPDATE` on the projection

Two events for the same `transaction_id` can arrive within milliseconds.
Without a row lock, both ingestion handlers can read the same "current
state," compute their own "next state" from it, and overwrite each other on
commit — losing whichever update lands first. Real-world impact: a `settled`
event arriving alongside a duplicate `processed` event could erase the
settlement. `SELECT ... FOR UPDATE` blocks the second handler until the
first commits, then it reads the up-to-date row and applies its transition
on top. At this scale the contention cost is negligible.

### What's deliberately out of scope

These are not built. Adding any of them half-built would be worse than
documenting the choice:

- **Authentication.** The brief doesn't require it. An API-key header would
  be a 15-minute add (`Security(APIKeyHeader(...))`).
- **Rate limiting.** Belongs at the load balancer or via `slowapi`, not in
  the application.
- **Retries / dead-letter queue.** The service is idempotent, so upstream
  retry-on-failure is safe and simpler than building a queue.
- **Per-merchant configuration** (different stuck-settlement thresholds,
  different currencies, etc.).
- **Metrics and tracing.** OpenTelemetry with the FastAPI auto-instrumentor
  would be the production answer.

### AI tool disclosure

Per the assignment's disclosure requirement:

- **Claude (Anthropic)** — used as a sounding board for the schema design,
  the state machine, and this README's content restructuring. The discrepancy lifecycle,
  the choice of projections and event logs, and the deployment approach were
  decisions I made.
- **GitHub Copilot** — used for boilerplate autocompletion (Pydantic
  schemas, test scaffolds, repetitive SQLAlchemy column definitions). All
  generated code was reviewed before commit.
