# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run uvicorn src.main:app --reload      # run the API (docs at /docs)
uv run pytest                             # full suite (31 tests, ~10s)
uv run pytest tests/test_detection.py -q               # one file
uv run pytest tests/test_scoring.py::test_name -q      # one test
uv run ruff check src tests               # lint (line-length 100, py312 target)
uv run ruff format src tests
```

`uv` only — never `pip`. `test.ipynb` at the repo root is the original POC notebook the
`src/` pipeline was extracted from; it is reference material, not part of the app.
`main.py` at the repo root is the unused `uv init` stub — the real entrypoint is
[src/main.py](src/main.py).

## Architecture

Read [README.md](README.md) first — it documents the CSV schema, the scoring formulas, and
every config field. The points below are the ones that constrain how you change the code.

**Signal-agnostic pipeline.** [src/services/io.py](src/services/io.py) `parse_csv` is the
*only* stage that knows whether the upload was `water_level` or `pressure_level`. It renames
the detected column to `settings.signal_column` (`signal`) and returns the original name as
`summary["signal_type"]`. Everything downstream — cleaning, events, profile, rules, scoring —
reads `signal` / `signal_smooth` with identical logic and thresholds. **Never add a branch on
signal type outside `parse_csv`.** The signal type is stamped onto the profile in
`_build_profile_sync` (not in `build_profile`) for the same reason, and `_build_detect_sync`
rejects a detect CSV whose signal type differs from the profile's.

**Stage chain** (each stage is a pure function taking `cfg: Settings`, so tests call them
directly without the API):

```
parse_csv → clean → extract_events → build_profile   (learn)
parse_csv → clean → extract_events → detect          (detect, against a stored profile)
```

**Async boundary.** Endpoints are `async` but do zero pandas work. All CPU-bound pipeline
work goes through `asyncio.to_thread` wrappers that live in
[src/core/helper.py](src/core/helper.py) (`_build_profile_sync`, `_build_detect_sync`). Those
wrappers use *function-local imports* deliberately, to avoid a `core → services` import cycle;
keep them local when editing.

**Learn is asynchronous, detect is synchronous.** `POST /v1/learn` stages the CSV to
`data/uploads/{profile_id}.csv`, writes a `pending` meta sidecar, enqueues
`learn_profile_task` via FastAPI `BackgroundTasks`, and returns `202` with a `profile_id`.
The client polls `GET /v1/profiles/{profile_id}` until `status == "ready"`. This runs
in-process, so it is not crash-durable across workers — the app compensates with a startup
sweeper (`_sweep_stale_tasks`) that marks `pending`/`running` metas older than
`task_timeout_minutes` as `failed`.

**Storage is the filesystem, two files per profile.**
[src/repositories/profile_store.py](src/repositories/profile_store.py) writes
`{id}.json` (the learned profile) and `{id}.meta.json` (status/summary/error). `exists()` and
status checks read the *meta*; a profile only has `{id}.json` once learning succeeded.
`profile_id` is a `uuid4().hex` and every store method validates it against `PROFILE_ID_RE`
(32 lowercase hex) before touching a path — keep that guard on any new path-taking method.
The store is a module-level singleton via `get_profile_store()`; tests reset
`profile_store._store = None` in the `clean_env` fixture.

**Config is the single source of truth for thresholds.** Everything tunable is a field on
`Settings` in [src/core/config.py](src/core/config.py), loaded from `src/.env` by
pydantic-settings. Never call `os.getenv()`. Profiles deliberately do **not** embed a config
snapshot, so changing a threshold retroactively changes the verdict for every stored profile.
Note `sensor_deadband` is magnitude-dependent (metres of level vs bar of pressure) and must be
retuned for a pressure deployment.

**Scoring is soft-gated, not cliff-gated.** Each of Rules A/B/C in
[src/services/detection.py](src/services/detection.py) returns a continuous
`score ∈ [0, 1]`; an alert is emitted at `score ≥ rule_min_score`, the day confidence is the
probabilistic union `1 - Π(1 - score_i)`, and `leak_detected` is
`confidence ≥ leak_confidence_threshold`. So weak alerts can appear in the response while
`leak_detected` stays false — this is intended, and tests rely on it.

**Errors.** Services raise the domain exceptions in [src/core/errors.py](src/core/errors.py)
(`ValidationError`, `ProfileNotFound`, `ProfileNotReady`); only the endpoint layer converts
them to `HTTPException`. Status mapping in use: 422 bad CSV/schema/signal-type mismatch,
413 upload too large, 404 unknown profile, 409 profile not `ready`, 500 unexpected.

## Conventions

- Every module starts with `from __future__ import annotations`.
- Logging via `get_logger(__name__)` with dotted event names and structured context, e.g.
  `logger.info("detect.complete", extra={"profile_id": ..., "confidence": ...})`. Use
  `logger.exception` inside `except`. No `print`.
- Tests build synthetic CSVs with the helpers in [tests/fixtures.py](tests/fixtures.py)
  (`make_series`, `slice_day`, `inject_slow_leak`, `df_to_csv_bytes`) — `make_series(column=...)`
  emits the same series as either measurement, which is how the signal-agnostic invariant is
  tested in [tests/test_signal_agnostic.py](tests/test_signal_agnostic.py). Use the `clean_env`
  fixture to point `profile_dir`/`uploads_dir` at `tmp_path`.
- `data/` holds real sample CSVs (30-day learn sets, single-day leak/pipe-break days) plus
  committed generated profiles; sample CSVs are useful for manual endpoint checks.
