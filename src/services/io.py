from __future__ import annotations

import io
from typing import Any

import pandas as pd

from src.core.config import Settings
from src.core.errors import ValidationError


def _longest_nan_run(mask: pd.Series) -> tuple[pd.Timestamp | None, int]:
    """(start timestamp, length) of the longest run of consecutive True values."""
    if not mask.any():
        return None, 0
    # every False increments the counter, so consecutive Trues share a group id
    groups = (~mask).cumsum()[mask]
    lengths = groups.value_counts()
    run = groups[groups == lengths.idxmax()]
    return run.index[0], int(lengths.max())


def parse_csv(
    file_bytes: bytes,
    cfg: Settings,
    expected_min_days: int | None = None,
    expected_min_minutes: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse, validate, clean cadence. Returns (df indexed by datetime, summary).

    This is the only stage that knows which measurement the CSV carried. The
    detected source column is renamed to `cfg.signal_column` and the rest of the
    pipeline operates on that generic name; the original name is reported back as
    `summary["signal_type"]` so the caller can pin a profile to its signal.
    """
    try:
        raw = pd.read_csv(io.BytesIO(file_bytes), parse_dates=["datetime"])
    except Exception as exc:
        raise ValidationError(f"unreadable CSV: {exc}") from exc

    if "datetime" not in raw.columns:
        raise ValidationError("missing required columns: ['datetime']")

    present = [c for c in cfg.signal_source_columns if c in raw.columns]
    if len(present) != 1:
        raise ValidationError(
            f"missing required columns: CSV must contain exactly one of "
            f"{list(cfg.signal_source_columns)}; found {present}"
        )
    signal_type = present[0]
    raw = raw.rename(columns={signal_type: cfg.signal_column})
    signal = cfg.signal_column

    if raw["datetime"].isna().any():
        raise ValidationError("datetime column contains null values")

    # nulls carried by the uploaded rows themselves; handled below, after the reindex,
    # so they take the same interpolate-or-reject path as rows inserted by asfreq
    n_null = int(raw[signal].isna().sum())

    raw = raw.sort_values("datetime").reset_index(drop=True)
    n_dup = int(raw["datetime"].duplicated().sum())
    raw = raw.drop_duplicates("datetime")

    dt_diff = raw["datetime"].diff().dt.total_seconds().dropna()
    inferred_interval_min = float(dt_diff.median() / 60.0) if len(dt_diff) else 0.0
    cadence_regular = bool(len(dt_diff) > 0 and (dt_diff == cfg.sampling_interval_seconds).all())
    if not cadence_regular:
        raise ValidationError(
            f"irregular cadence: inferred {inferred_interval_min:.1f} min vs "
            f"expected {cfg.sampling_interval_seconds / 60:.1f} min"
        )

    expected_td = pd.Timedelta(seconds=cfg.sampling_interval_seconds)
    df = raw.set_index("datetime").sort_index()
    df = df.asfreq(expected_td)
    if df.empty:
        raise ValidationError("CSV contains no usable rows")

    # every NaN cell in the reindexed frame: nulls the upload carried plus rows asfreq
    # inserted for missing timestamps
    n_nan = int(df[signal].isna().sum())
    n_filled = 0
    if n_nan:
        limit = cfg.max_interpolate_samples
        start, longest = _longest_nan_run(df[signal].isna())
        if longest > limit:
            raise ValidationError(
                f"signal column has an unfillable gap of {longest} consecutive null "
                f"values starting at {start.isoformat()}: at most {limit} in a row "
                f"can be interpolated"
            )
        # run lengths are already vetted, so no `limit` here; limit_direction="both"
        # is what lets a run at the very start of the series be filled at all — the
        # forward-only default can never fill leading nulls, however few there are
        df[signal] = df[signal].interpolate(method="time", limit_direction="both")
        n_filled = n_nan - int(df[signal].isna().sum())

    summary: dict[str, Any] = {
        "signal_type": signal_type,
        "rows": int(len(df)),
        "start": df.index[0].isoformat(),
        "end": df.index[-1].isoformat(),
        "inferred_interval_min": inferred_interval_min,
        "cadence_regular": cadence_regular,
        "duplicate_rows": n_dup,
        "null_values": n_null,
        "missing_values": n_nan,
        "filled_gaps": n_filled,
        "signal_min": float(df[signal].min()),
        "signal_max": float(df[signal].max()),
        "signal_mean": float(df[signal].mean()),
        "span_days": float((df.index[-1] - df.index[0]).total_seconds() / 86400.0),
        "span_minutes": float((df.index[-1] - df.index[0]).total_seconds() / 60.0),
    }

    if expected_min_days is not None and summary["span_days"] < expected_min_days:
        raise ValidationError(
            f"series too short for learning: {summary['span_days']:.1f} days "
            f"< required {expected_min_days} days"
        )
    if expected_min_minutes is not None and summary["span_minutes"] + 5 < expected_min_minutes:
        raise ValidationError(
            f"series too short for detection: {summary['span_minutes']:.0f} min "
            f"< required {expected_min_minutes} min"
        )

    return df, summary
