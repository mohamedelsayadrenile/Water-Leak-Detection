# AGENTS.md

Compact agent cheat-sheet. Read `README.md` (CSV schema, scoring formulas, full config table)
and `CLAUDE.md` (deeper architectural constraints) for the full picture.

## Commands

`uv` only — never `pip`.

```bash
uv run uvicorn src.main:app --reload        # API (docs at /docs)
uv run pytest                                # full suite
uv run pytest tests/test_detection.py -q                 # one file
uv run pytest tests/test_scoring.py::test_name -q        # one test
uv run ruff check src tests                  # lint (line-length 100, py312)
uv run ruff format src tests
```

verify order: `ruff check` -> `pytest`. No CI, no pre-commit, so these must be run manually.

## Entrypoints (not obvious from filenames)

- Real app entrypoint is `src/main.py`. Root `main.py` is the unused `uv init` stub.
- `test.ipynb` at repo root is the original POC notebook — reference material, not part of the app. Do not edit or import it.

## Architectural constraints an agent would likely violate

1. **Signal-agnostic pipeline.** Only `src/services/io.py` `parse_csv` knows whether the upload
   was `water_level` or `pressure_level`; it renames the column to `signal` and records the
   original as `signal_type`. **Never add a branch on `signal_type` anywhere else** (cleaning,
   events, profile, rules, scoring all use identical logic/thresholds). Signal type is stamped
   onto the profile in `_build_profile_sync`, and `_build_detect_sync` rejects a CSV whose type
   differs.
2. **Async boundary.** Endpoints are `async` and do zero pandas work. All CPU-bound pipeline
   work goes through `asyncio.to_thread` wrappers `_build_profile_sync` / `_build_detect_sync`
   in `src/core/helper.py`. Those wrappers use *function-local imports* deliberately to avoid a
   `core -> services` import cycle — keep them local when editing.
3. **No `os.getenv()`.** Every tunable is a field on `Settings` in `src/core/config.py`, loaded
   from `src/.env` by pydantic-settings. Profiles deliberately do **not** embed a config
   snapshot — changing a threshold retroactively changes the verdict for every stored profile.
   Note `sensor_deadband` is magnitude-dependent (m of level vs bar of pressure) and must be
   retuned for a pressure deployment.
4. **`profile_id` is `uuid4().hex`** (32 lowercase hex). Every `profile_store` method validates
   it against `PROFILE_ID_RE` before touching a path — keep that guard on any new path-taking
   method.

## Learn vs detect (different execution models)

- `POST /v1/learn` stages the CSV to `data/uploads/{profile_id}.csv`, writes a `pending` meta
  sidecar, enqueues `learn_profile_task` via FastAPI `BackgroundTasks`, returns `202` with
  `profile_id`. Client polls `GET /v1/profiles/{profile_id}` until `status == "ready"`.
- `BackgroundTasks` runs in-process — **not crash-durable** across workers. Compensated by a
  startup sweeper (`_sweep_stale_tasks`) that marks `pending`/`running` metas older than
  `task_timeout_minutes` as `failed`.
- `POST /v1/detect` is synchronous and returns the verdict immediately.

## Storage (filesystem, two files per profile)

`src/repositories/profile_store.py` writes `{id}.json` (learned profile) and `{id}.meta.json`
(status/summary/error). `exists()` and status checks read the **meta**; `{id}.json` only exists
once learning succeeded. Module-level singleton via `get_profile_store()`; tests reset
`profile_store._store = None` in the `clean_env` fixture.

## Scoring is soft-gated, not cliff-gated

Each Rule A/B/C in `src/services/detection.py` returns a continuous `score ∈ [0, 1]`. An alert
is emitted at `score ≥ rule_min_score`; day confidence is the probabilistic union
`1 - Π(1 - score_i)`; `leak_detected = confidence ≥ leak_confidence_threshold`. **Weak alerts
can appear in the response while `leak_detected` stays false** — this is intended and tests rely
on it. Do not "fix" it.

## Errors

Services raise domain exceptions in `src/core/errors.py`
(`ValidationError`, `ProfileNotFound`, `ProfileNotReady`); **only the endpoint layer converts
them to `HTTPException`**. Status mapping: 422 bad CSV/schema/signal-type mismatch, 413 upload
too large, 404 unknown profile, 409 profile not `ready`, 500 unexpected.

## Conventions

- Every module starts with `from __future__ import annotations`.
- Logging via `get_logger(__name__)` with dotted event names and structured context, e.g.
  `logger.info("detect.complete", extra={"profile_id": ..., "confidence": ...})`. Use
  `logger.exception` inside `except`. No `print`. Never log secrets/keys/tokens.
- Tests build synthetic CSVs with helpers in `tests/fixtures.py`
  (`make_series`, `slice_day`, `inject_slow_leak`, `df_to_csv_bytes`). `make_series(column=...)`
  emits the same series as either measurement — this is how the signal-agnostic invariant is
  tested in `tests/test_signal_agnostic.py`. Use the `clean_env` fixture to point
  `profile_dir`/`uploads_dir` at `tmp_path`.
- `data/` holds real sample CSVs (30-day learn sets, single-day leak/pipe-break days) plus
  committed generated profiles — useful for manual endpoint checks.
- pytest config: `pythonpath = ["."]`, `testpaths = ["tests"]`. ruff: `target-version py312`,
  `select = ["E","F","I","W","B","UP"]`, `ignore = ["E501","B008"]`.