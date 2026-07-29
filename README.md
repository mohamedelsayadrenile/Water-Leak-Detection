# Water Leak Detection

Behavior-based, explainable water-tank leak detection from a single `water_level`
signal. Built on the `test.ipynb` POC.

Two FastAPI endpoints:

1. `POST /v1/learn` — upload a **1-month** CSV; the system learns the user's
   consumption profile **in the background** and returns a `profile_id`
   immediately (`202 Accepted`). Poll `GET /v1/profiles/{profile_id}` for
   completion.
2. `POST /v1/detect` — upload **1-day** CSV plus a `profile_id`; the system
   returns synchronously whether a leak is suspected and which rules fired.

## CSV schema

```
datetime,water_level
2025-04-01 00:00:00,4.000
...
```

- 5-minute sampling cadence (configurable).
- Headers must contain `datetime` and `water_level`.

## Run

```bash
uv run uvicorn src.main:app --reload
```

## Layout

```
src/
├── main.py                # FastAPI app, startup sweeper for stale tasks
├── core/                  # config (pydantic-settings), logging, errors
├── services/              # pure pipeline stages (cleaning/events/profile/detection) + learn_task
├── repositories/          # profile_store (filesystem JSON + meta sidecar)
├── models/schemas/        # request/response pydantic models
└── api/v1/endpoints/      # learn, detect, profiles (poll)
```

## How it works

Pipeline (all pandas work runs off the event loop via `asyncio.to_thread`):

1. **parse_csv** — validate schema, cadence, length; dedup; interpolate ≤3-sample gaps.
2. **clean** — rolling median, deadband movement classification, refill mask.
3. **extract_events** — continuous drain segments → event rows.
4. **build_profile** (learn only) — per-hour activity probability, per-hour
   duration p95, quiet hours, global duration stats, quiet baseline slope.
5. **detect** (detect only, against the stored profile) — three rules:
   - **A** unusual-hour long drain (start in a quiet hour, duration > p95 × 1.5).
   - **B** slow night leak (sliding `linregress` over quiet hours: significant
     negative slope exceeding baseline).
   - **C** hard duration cap (> 4 h).

## Background learning

`POST /v1/learn` stages the upload to `data/uploads/{profile_id}.csv`, writes a
pending `data/profiles/{profile_id}.meta.json`, enqueues
`learn_profile_task` via FastAPI `BackgroundTasks`, and returns immediately.
The task runs the pipeline in a worker thread, writes the profile, flips meta to
`ready` (with summary), and deletes the staged CSV. On failure meta becomes
`failed` with the error message.

At startup the app sweeps any `pending`/`running` meta older than
`task_timeout_minutes` and marks them `failed` (self-healing after crashes).

## Configuration

All tunables live in `src/.env/.env` (loaded by pydantic-settings). No
`os.getenv()` calls in code. Key fields:

| Field | Default | Meaning |
|---|---|---|
| `min_learn_days` | 25 | reject learn CSVs shorter than this |
| `min_detect_minutes` | 1440 | reject detect CSVs shorter than this |
| `max_upload_mb` | 50 | upload size cap |
| `task_timeout_minutes` | 60 | stale-task sweeper threshold |
| `sweep_stale_tasks_on_startup` | true | run sweeper at app start |
| `rule_c_max_duration_hours` | 4.0 | hard leak duration cap |
| `profile_dir` / `uploads_dir` | `data/...` | storage roots |

Threshold changes affect all stored profiles (global config is the single source
of truth; profiles do not embed the config).

## Tests

```bash
uv run pytest
uv run ruff check src tests
```

## Notes / caveats

- **Rule B on 1-day detects:** quiet-hour windows can be short in a single day,
  so Recall on the detect endpoint is inherently lower than the POC's 30-day
  run.
- `BackgroundTasks` runs in-process; for multi-worker or crash-durable
  scheduling, move `learn_profile_task` to an external worker (ARQ/RQ + Redis).
  The task function signature stays the same.