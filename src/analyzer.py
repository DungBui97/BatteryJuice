"""
Advisory health signal analysis.

All signals are OBSERVATIONS FOR REFERENCE ONLY — not diagnoses.
Forbidden words (never appear in signal messages):
  damaged, faulty, replace, failing, broken, defective
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

# ── tone constants ────────────────────────────────────────────────────────────
LEVEL_CAUTION = "caution"
LEVEL_WARNING = "warning"
LEVEL_INFO    = "info"

# Apple rated cycles by model generation (conservative defaults)
RATED_CYCLES = {
    "default": 1000,   # M1+ MacBooks
    "old":     500,    # Pre-2013 MacBooks
}

# Capacity threshold Apple uses to suggest service: 80% of design
SERVICE_THRESHOLD_PCT = 80.0

# Minimum days of data required before surfacing trend signals
MIN_DATA_DAYS = 14


# ── helpers ───────────────────────────────────────────────────────────────────

def _linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Return (slope, intercept) for least-squares fit."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def _days_since(iso_ts: str) -> float:
    dt = datetime.fromisoformat(iso_ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _parse_ts(iso_ts: str) -> datetime:
    dt = datetime.fromisoformat(iso_ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _epoch_days(iso_ts: str) -> float:
    dt = _parse_ts(iso_ts)
    return dt.timestamp() / 86400


def _date_span_days(snapshots: list[dict]) -> float:
    if len(snapshots) < 2:
        return 0.0
    return _days_since(snapshots[0]["timestamp"])


# ── capacity-based life estimate (works even when always plugged in) ──────────

def capacity_based_estimate(snapshots: list[dict], design_cap: int) -> dict | None:
    """
    Estimate months until battery reaches 80% design capacity
    using linear regression on max_capacity_mah over time.

    This method is reliable regardless of charging habits —
    even users who keep the Mac always plugged in will see
    gradual natural capacity degradation over time.
    """
    valid = [s for s in snapshots if s.get("max_capacity_mah") and s.get("design_capacity_mah")]
    if len(valid) < 10:
        return None

    xs = [_epoch_days(s["timestamp"]) for s in valid]
    ys = [s["max_capacity_mah"] for s in valid]

    slope, intercept = _linear_regression(xs, ys)

    # slope is mAh per day (negative = losing capacity)
    if slope >= 0:
        # No degradation detected yet
        return {
            "method": "capacity",
            "slope_mah_per_day": round(slope, 4),
            "trend": "stable",
            "months_to_service": None,
            "current_health_pct": round(ys[-1] / design_cap * 100, 1),
        }

    target_mah = design_cap * SERVICE_THRESHOLD_PCT / 100
    current_x = xs[-1]
    current_y = intercept + slope * current_x

    if current_y <= target_mah:
        return {
            "method": "capacity",
            "slope_mah_per_day": round(slope, 4),
            "trend": "below_threshold",
            "months_to_service": 0,
            "current_health_pct": round(current_y / design_cap * 100, 1),
        }

    days_remaining = (target_mah - current_y) / slope  # slope is negative
    months_remaining = max(0, round(days_remaining / 30))

    return {
        "method": "capacity",
        "slope_mah_per_day": round(slope, 4),
        "trend": "degrading",
        "months_to_service": months_remaining,
        "current_health_pct": round(current_y / design_cap * 100, 1),
    }


# ── cycle-based life estimate ─────────────────────────────────────────────────

def cycle_based_estimate(snapshots: list[dict], rated_cycles: int = 1000) -> dict | None:
    """
    Estimate months until rated cycle count based on observed cycle burn rate.
    Less reliable for always-plugged users (cycles accumulate slowly).
    """
    valid = [s for s in snapshots if s.get("cycle_count") is not None]
    if len(valid) < 2:
        return None

    span_days = _date_span_days(valid)
    if span_days < 7:
        return None

    first_cycles = valid[0]["cycle_count"]
    last_cycles = valid[-1]["cycle_count"]
    cycles_gained = last_cycles - first_cycles

    if cycles_gained <= 0:
        return {
            "method": "cycle",
            "cycles_per_month": 0,
            "current_cycles": last_cycles,
            "rated_cycles": rated_cycles,
            "months_to_rated": None,
        }

    cycles_per_day = cycles_gained / span_days
    cycles_per_month = round(cycles_per_day * 30, 1)
    cycles_remaining = rated_cycles - last_cycles
    if cycles_remaining <= 0:
        months_remaining = 0  # already at or past rated cycle count
    elif cycles_per_day > 0:
        months_remaining = max(0, round(cycles_remaining / cycles_per_day / 30))
    else:
        months_remaining = None

    return {
        "method": "cycle",
        "cycles_per_month": cycles_per_month,
        "current_cycles": last_cycles,
        "rated_cycles": rated_cycles,
        "months_to_rated": months_remaining,
    }


# ── always-plugged detection ──────────────────────────────────────────────────

def always_plugged_pattern(snapshots: list[dict]) -> dict | None:
    recent = [s for s in snapshots if _days_since(s["timestamp"]) <= 30]
    if len(recent) < 10:
        return None
    plugged_count = sum(1 for s in recent if s.get("is_charging"))
    ratio = plugged_count / len(recent)
    if ratio < 0.75:
        return None
    return {
        "level": LEVEL_INFO,
        "ratio": round(ratio * 100),
        "message": (
            "Your Mac is frequently kept plugged in. "
            "Cycle count may rise slowly — capacity trend over time "
            "is a more reliable health indicator for your usage pattern."
        ),
    }


# ── trend signals ─────────────────────────────────────────────────────────────

def _capacity_loss_rate_signal(snapshots: list[dict]) -> dict | None:
    valid = [s for s in snapshots if s.get("max_capacity_mah") and _days_since(s["timestamp"]) <= 30]
    if len(valid) < 5:
        return None
    xs = [_epoch_days(s["timestamp"]) for s in valid]
    ys = [s["max_capacity_mah"] for s in valid]
    slope, _ = _linear_regression(xs, ys)
    loss_per_day = -slope  # positive = losing

    if loss_per_day >= 2:
        level = LEVEL_WARNING
        msg = "Capacity is declining faster than typical — worth keeping an eye on over the next few weeks."
    elif loss_per_day >= 1:
        level = LEVEL_CAUTION
        msg = "Capacity shows a slight downward trend. This may be normal variation — you may want to monitor it."
    else:
        return None

    return {"signal": "capacity_loss_rate", "level": level, "value": round(loss_per_day, 2),
            "unit": "mAh/day", "message": msg}


def _health_acceleration_signal(snapshots: list[dict]) -> dict | None:
    now_epoch = datetime.now(timezone.utc).timestamp() / 86400

    def loss_rate(days_start, days_end):
        window = [s for s in snapshots
                  if days_start <= (now_epoch - _epoch_days(s["timestamp"])) < days_end
                  and s.get("max_capacity_mah")]
        if len(window) < 5:
            return None
        xs = [_epoch_days(s["timestamp"]) for s in window]
        ys = [s["max_capacity_mah"] for s in window]
        slope, _ = _linear_regression(xs, ys)
        return -slope

    recent = loss_rate(0, 30)
    prior = loss_rate(30, 60)
    if recent is None or prior is None or prior <= 0:
        return None

    change_pct = (recent - prior) / prior * 100
    if change_pct >= 50:
        level = LEVEL_WARNING
        msg = "Capacity is declining noticeably faster than last month — declining faster than typical recently."
    elif change_pct >= 20:
        level = LEVEL_CAUTION
        msg = "Capacity loss rate is slightly higher than the previous month. Worth keeping an eye on."
    else:
        return None

    return {"signal": "health_acceleration", "level": level, "value": round(change_pct, 1),
            "unit": "%_increase_in_loss_rate", "message": msg}


def _cycle_burn_signal(snapshots: list[dict]) -> dict | None:
    recent = [s for s in snapshots
              if _days_since(s["timestamp"]) <= 30 and s.get("cycle_count") is not None]
    if len(recent) < 2:
        return None
    span = _days_since(recent[0]["timestamp"])
    if span < 7:
        return None
    delta = recent[-1]["cycle_count"] - recent[0]["cycle_count"]
    per_month = delta / span * 30

    if per_month >= 50:
        level = LEVEL_WARNING
        msg = f"Cycle count is rising quickly (~{round(per_month)}/month). You may want to monitor battery usage habits."
    elif per_month >= 30:
        level = LEVEL_CAUTION
        msg = f"Cycle count is rising at ~{round(per_month)}/month — slightly above typical. Worth keeping an eye on."
    else:
        return None

    return {"signal": "cycle_burn_rate", "level": level, "value": round(per_month, 1),
            "unit": "cycles/month", "message": msg}


def _drain_anomaly_signal(snapshots: list[dict]) -> dict | None:
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    month_ago = datetime.now(timezone.utc) - timedelta(days=30)

    def avg_draw(start_dt, end_dt):
        window = [s for s in snapshots
                  if s.get("power_draw_w") and not s.get("is_charging")
                  and start_dt <= _parse_ts(s["timestamp"]) <= end_dt]
        if not window:
            return None
        return sum(s["power_draw_w"] for s in window) / len(window)

    now = datetime.now(timezone.utc)
    week_avg = avg_draw(week_ago, now)
    month_avg = avg_draw(month_ago, week_ago)

    if week_avg is None or month_avg is None or month_avg <= 0:
        return None

    diff_pct = (week_avg - month_avg) / month_avg * 100
    if diff_pct >= 40:
        level = LEVEL_WARNING
        msg = f"Power draw this week (~{week_avg:.1f}W) is notably higher than your 30-day average (~{month_avg:.1f}W)."
    elif diff_pct >= 20:
        level = LEVEL_CAUTION
        msg = f"Power draw this week (~{week_avg:.1f}W) is slightly above your recent average (~{month_avg:.1f}W)."
    else:
        return None

    return {"signal": "drain_anomaly", "level": level, "value": round(diff_pct, 1),
            "unit": "%_above_baseline", "message": msg}


def _charge_habit_signals(snapshots: list[dict]) -> list[dict]:
    recent = [s for s in snapshots if _days_since(s["timestamp"]) <= 30]
    if len(recent) < 10:
        return []

    signals = []

    # Frequent full charges (ending at 100%)
    full_charges = sum(1 for s in recent if s.get("current_pct", 0) >= 99 and not s.get("is_charging"))
    total_discharge = sum(1 for s in recent if not s.get("is_charging"))
    if total_discharge > 0:
        ratio = full_charges / total_discharge
        if ratio >= 0.8:
            signals.append({
                "signal": "frequent_full_charge",
                "level": LEVEL_WARNING,
                "value": round(ratio * 100),
                "unit": "%_sessions_at_100pct",
                "message": "Many discharge sessions start near 100%. Keeping charge between 20–80% can help preserve long-term capacity.",
            })
        elif ratio >= 0.5:
            signals.append({
                "signal": "frequent_full_charge",
                "level": LEVEL_CAUTION,
                "value": round(ratio * 100),
                "unit": "%_sessions_at_100pct",
                "message": "Charge often reaches 100%. This is fine occasionally — you may want to monitor if this is consistent.",
            })

    # Deep discharge (starting from <10%)
    deep = sum(1 for s in recent if s.get("current_pct", 100) < 10 and not s.get("is_charging"))
    charge_starts = sum(1 for s in recent if s.get("is_charging"))
    if charge_starts > 0:
        ratio = deep / charge_starts
        if ratio >= 0.5:
            signals.append({
                "signal": "deep_discharge",
                "level": LEVEL_WARNING,
                "value": round(ratio * 100),
                "unit": "%_sessions_below_10pct",
                "message": "Battery frequently runs very low before charging. Charging above 20% may help over time.",
            })
        elif ratio >= 0.3:
            signals.append({
                "signal": "deep_discharge",
                "level": LEVEL_CAUTION,
                "value": round(ratio * 100),
                "unit": "%_sessions_below_10pct",
                "message": "Battery occasionally runs very low — worth keeping an eye on if this is a regular pattern.",
            })

    return signals


def _temp_alert(snapshot: dict) -> dict | None:
    if not snapshot.get("is_charging"):
        return None
    temp = snapshot.get("temperature_c")
    if temp is None or temp < 45:
        return None
    return {
        "signal": "high_temp_charging",
        "level": LEVEL_WARNING,
        "value": temp,
        "unit": "°C",
        "message": f"Battery temperature is {temp}°C while charging. High heat during charging may affect long-term capacity.",
        "immediate": True,
    }


# ── main entry point ──────────────────────────────────────────────────────────

def analyze(snapshots: list[dict], config: dict, latest: dict | None = None) -> dict:
    """
    Run all signals and estimates against the snapshot history.
    Returns a structured result with signals, estimates, and plugged pattern.

    All results are advisory — never diagnostic.
    """
    if not snapshots:
        return {"signals": [], "estimates": {}, "plugged_pattern": None, "has_enough_data": False}

    span_days = _date_span_days(snapshots)
    has_enough = span_days >= MIN_DATA_DAYS

    signals: list[dict] = []

    # Immediate temp alert (real-time, no history needed)
    if latest:
        temp_sig = _temp_alert(latest)
        if temp_sig:
            signals.append(temp_sig)

    if has_enough:
        for fn in [
            _capacity_loss_rate_signal,
            _health_acceleration_signal,
            _cycle_burn_signal,
            _drain_anomaly_signal,
        ]:
            sig = fn(snapshots)
            if sig:
                signals.append(sig)

        signals.extend(_charge_habit_signals(snapshots))

    # Estimates
    design_cap = (latest or {}).get("design_capacity_mah") or (
        next((s["design_capacity_mah"] for s in reversed(snapshots) if s.get("design_capacity_mah")), None)
    )
    rated_cycles = config.get("apple_rated_cycles", 1000)

    cap_est = capacity_based_estimate(snapshots, design_cap) if design_cap else None
    cyc_est = cycle_based_estimate(snapshots, rated_cycles)

    # Determine which estimate to lead with
    plugged = always_plugged_pattern(snapshots)
    prefer_capacity = (
        plugged is not None
        or (cyc_est and cyc_est.get("cycles_per_month", 0) < 5)
    )

    return {
        "signals": signals,
        "estimates": {
            "capacity_based": cap_est,
            "cycle_based": cyc_est,
            "prefer_capacity": prefer_capacity,
        },
        "plugged_pattern": plugged,
        "has_enough_data": has_enough,
        "span_days": round(span_days),
    }
