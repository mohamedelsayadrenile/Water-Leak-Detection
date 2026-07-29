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
5. **detect** (detect only, against the stored profile) — three rules, each
   computing a **confidence score** in `[0, 1]` (soft gates). An alert is emitted
   when `score ≥ rule_min_score`; the day-level `leak_confidence` is the
   probabilistic union `1 - Π(1 - score_i)` of all alert scores; the verdict is
   `leak_detected = leak_confidence ≥ leak_confidence_threshold`.

   Scoring formulas:
   - **Rule A** — unusual-hour long drain:
     `score = unusualness · duration_term`, where
     `unusualness = clamp01(1 - prob / rule_a_min_unusual_prob)` and
     `duration_term = min(1, duration / (2 · rule_a_dur_mult · p95))`.
   - **Rule B** — slow night leak (`linregress` slope over quiet-hour windows):
     `score = significance · strength`, where
     `significance = min(1, -log10(max(p, 1e-300)) / 10)` and
     `strength = min(1, (|slope| / max(|baseline|·rule_b_slope_mult, 1e-5)) / 10)`.
     Positive slopes score 0.
   - **Rule C** — duration cap:
     `score = min(1, duration / (2 · rule_c_max_duration_hours · 60))`.

   Severity bands: `none (< threshold)` | `low (< 0.6)` | `medium (< 0.85)` |
   `high (≥ 0.85)`.

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
| `rule_c_max_duration_hours` | 4.0 | leak duration cap (half of the score reference) |
| `rule_min_score` | 0.3 | per-rule alert emission threshold (soft gate) |
| `leak_confidence_threshold` | 0.5 | day-level verdict threshold for `leak_detected` |
| `severity_medium_threshold` | 0.6 | low → medium severity boundary |
| `severity_high_threshold` | 0.85 | medium → high severity boundary |
| `profile_dir` / `uploads_dir` | `data/...` | storage roots |

Threshold changes affect all stored profiles (global config is the single source
of truth; profiles do not embed the config).

## Tests

```bash
uv run pytest
uv run ruff check src tests
```

## Notes / caveats

- **Soft gates:** rules no longer fire on hard cliffs (prob < 0.25, duration >
  p95 × 1.5, > 4 h, p < alpha). Evidence is now continuous; weak alerts can
  appear in `alerts` while `leak_detected` stays false when day confidence is
  below `leak_confidence_threshold`. The old Rule B `sensor_deadband/5` absolute
  floor was removed — it blocked real slow leaks (the trained baseline +
  p-value comparison already encodes the "is this slope real" test).
- **Rule B on 1-day detects:** quiet-hour windows can be short in a single day,
  so Recall on the detect endpoint is inherently lower than the POC's 30-day
  run.
- `BackgroundTasks` runs in-process; for multi-worker or crash-durable
  scheduling, move `learn_profile_task` to an external worker (ARQ/RQ + Redis).
  The task function signature stays the same.