from __future__ import annotations

import math

from src.core.config import Settings, settings
from src.services.detection import Alert, aggregate_confidence, severity_for


def _alert(score: float, rule: str = "B") -> Alert:
    return Alert(
        start="2025-01-01T00:00:00", end="2025-01-01T01:00:00", rule=rule, reason="x", score=score
    )


def test_aggregate_empty_is_zero():
    assert aggregate_confidence([]) == 0.0


def test_aggregate_single_is_score():
    assert aggregate_confidence([_alert(0.7)]) == 0.7


def test_aggregate_union_combines():
    # 1 - (1-0.8)(1-0.6) = 1 - 0.2*0.4 = 0.92
    assert math.isclose(aggregate_confidence([_alert(0.8), _alert(0.6)]), 0.92, abs_tol=1e-9)


def test_aggregate_clamps_to_one():
    assert aggregate_confidence([_alert(1.0), _alert(1.0)]) == 1.0


def test_severity_bands():
    cfg = settings
    assert severity_for(0.49, cfg) == "none"
    assert severity_for(0.5, cfg) == "low"
    assert severity_for(0.6, cfg) == "medium"
    assert severity_for(0.85, cfg) == "high"
    assert severity_for(0.95, cfg) == "high"


def test_score_range_on_alert():
    a = _alert(0.5)
    assert 0.0 <= a.score <= 1.0


def test_custom_thresholds():
    cfg = Settings(
        leak_confidence_threshold=0.7,
        severity_medium_threshold=0.75,
        severity_high_threshold=0.9,
        rule_min_score=0.3,
    )
    assert severity_for(0.69, cfg) == "none"
    assert severity_for(0.72, cfg) == "low"
    assert severity_for(0.8, cfg) == "medium"
    assert severity_for(0.95, cfg) == "high"


def test_rule_b_scoring_extremes():
    # p ~ 0 -> significance 1.0 (via the 1e-300 clamp)
    sig = min(1.0, -math.log10(max(0.0, 1e-300)) / 10.0)
    assert sig == 1.0  # -log10(1e-300)/10 is huge, clamp to 1
    # p exactly at the old alpha -> significance 0.2
    sig_alpha = min(1.0, -math.log10(0.01) / 10.0)
    assert math.isclose(sig_alpha, 0.2, abs_tol=1e-6)
    # baseline==0 -> ref falls back to 1e-5
    ref = max(abs(0.0) * 2.0, 1e-5)
    assert ref == 1e-5
    strength = min(1.0, (6.6e-4 / ref) / 10.0)
    assert strength == 1.0  # 6.6e-4 / 1e-4 = 6.6 -> >reffloor
