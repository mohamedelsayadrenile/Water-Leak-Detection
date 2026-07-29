from __future__ import annotations

import io
from typing import Any

import pandas as pd

from src.core.config import Config
from src.core.errors import ValidationError


def parse_csv(
    file_bytes: bytes,
    cfg: Config,
    expected_min_days: int | None = None,
    expected_min_minutes: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse, validate, clean cadence. Returns (df indexed by datetime, summary)."""
    try:
        raw = pd.read_csv(io.BytesIO(file_bytes), parse_dates=["datetime"])
    except Exception as exc:
        raise ValidationError(f"unreadable CSV: {exc}") from exc

    required = {"datetime", "water_level"}
    missing = required - set(raw.columns)
    if missing:
        raise ValidationError(f"missing required columns: {sorted(missing)}")

    if raw["datetime"].isna().any():
        raise ValidationError("datetime column contains null values")

    if raw["water_level"].isna().any():
        # interpolate small gaps AFTER reindex; for now reject fully-missing rows
        n_missing = int(raw["water_level"].isna().sum())
    else:
        n_missing = 0

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
    n_gaps = int(df["water_level"].isna().sum())
    n_filled = 0
    if n_gaps:
        n_filled = min(n_gaps, 3 * int(n_gaps))
        df["water_level"] = df["water_level"].interpolate(method="time", limit=3)
        still_nan = int(df["water_level"].isna().sum())
        if still_nan:
            raise ValidationError(
                f"unfillable gaps in series: {still_nan} samples still NaN after "
                f"interpolation (limit=3)"
            )

    summary: dict[str, Any] = {
        "rows": int(len(df)),
        "start": df.index[0].isoformat(),
        "end": df.index[-1].isoformat(),
        "inferred_interval_min": inferred_interval_min,
        "cadence_regular": cadence_regular,
        "duplicate_rows": n_dup,
        "missing_values": n_missing + n_gaps,
        "filled_gaps": n_filled,
        "level_min": float(df["water_level"].min()),
        "level_max": float(df["water_level"].max()),
        "level_mean": float(df["water_level"].mean()),
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
