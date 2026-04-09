"""HTML report generation with Chart.js charts."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from database import get_snapshots, get_latest_snapshot, log_report
from analyzer import analyze


def _chartjs_script() -> str:
    """Return Chart.js as an inline <script> block.
    Looks for a vendored copy in assets/ (works bundled and from source).
    Falls back to CDN — charts won't render offline in that case.
    """
    candidates = [
        # PyInstaller bundle: _MEIPASS/assets/
        Path(getattr(__import__("sys"), "_MEIPASS", "")) / "assets" / "chart.umd.min.js",
        # Running from source: repo_root/assets/
        Path(__file__).parent.parent / "assets" / "chart.umd.min.js",
    ]
    for path in candidates:
        if path.exists():
            js = path.read_text(encoding="utf-8")
            return f"<script>{js}</script>"
    # Fallback: CDN (requires internet)
    return '<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>'


DISCLAIMER = (
    "These observations are based on recorded usage data and are for reference only. "
    "They do not constitute a diagnosis. For authoritative battery assessment, "
    "use Apple Diagnostics or visit an Apple Store."
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _daily_aggregates(snapshots: list[dict]) -> list[dict]:
    by_day: dict[str, list] = defaultdict(list)
    for s in snapshots:
        day = _parse_ts(s["timestamp"]).strftime("%Y-%m-%d")
        by_day[day].append(s)

    result = []
    for day in sorted(by_day):
        group = by_day[day]

        def avg(field):
            vals = [s[field] for s in group if s.get(field) is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        design = next((s["design_capacity_mah"] for s in group if s.get("design_capacity_mah")), None)
        max_cap = avg("max_capacity_mah")
        health = round(max_cap / design * 100, 1) if max_cap and design else None

        result.append({
            "date": day,
            "health_pct": health,
            "max_capacity_mah": max_cap,
            "cycle_count": avg("cycle_count"),
            "avg_draw_w": avg("power_draw_w"),
            "avg_temp_c": avg("temperature_c"),
            "min_charge_pct": min((s["current_pct"] for s in group if s.get("current_pct") is not None), default=None),
            "max_charge_pct": max((s["current_pct"] for s in group if s.get("current_pct") is not None), default=None),
        })
    return result


def _charging_habit_breakdown(snapshots: list[dict]) -> dict:
    discharging = [s for s in snapshots if not s.get("is_charging") and s.get("current_pct") is not None]
    full = sum(1 for s in discharging if s["current_pct"] >= 99)
    deep = sum(1 for s in discharging if s["current_pct"] < 10)
    normal = len(discharging) - full - deep
    return {
        "full_pct": round(full / len(discharging) * 100) if discharging else 0,
        "deep_pct": round(deep / len(discharging) * 100) if discharging else 0,
        "normal_pct": round(normal / len(discharging) * 100) if discharging else 0,
    }


def _charge_timeline(snapshots: list[dict], max_points: int = 200) -> list[dict]:
    if not snapshots:
        return []
    step = max(1, len(snapshots) // max_points)
    return [
        {
            "ts": _parse_ts(s["timestamp"]).strftime("%Y-%m-%d %H:%M"),
            "pct": s.get("current_pct"),
            "charging": bool(s.get("is_charging")),
        }
        for s in snapshots[::step]
        if s.get("current_pct") is not None
    ]


def generate_report(db_path: str, output_dir: str, period_days: int, config: dict) -> str:
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=period_days)
    snapshots = get_snapshots(db_path, start.isoformat(), end.isoformat())
    latest = get_latest_snapshot(db_path)

    daily = _daily_aggregates(snapshots)
    habits = _charging_habit_breakdown(snapshots)
    timeline = _charge_timeline(snapshots)
    analysis = analyze(snapshots, config, latest)

    # Summary stats
    current_health = None
    current_cycles = None
    design_cap = None
    max_cap = None
    if latest:
        design_cap = latest.get("design_capacity_mah")
        max_cap = latest.get("max_capacity_mah")
        current_cycles = latest.get("cycle_count")
        if design_cap and max_cap:
            current_health = round(max_cap / design_cap * 100, 1)

    first_health = daily[0]["health_pct"] if daily else None
    capacity_lost = None
    if daily and daily[0].get("max_capacity_mah") and max_cap:
        diff = daily[0]["max_capacity_mah"] - max_cap
        capacity_lost = round(diff) if diff > 0 else None  # only show if actually lost

    avg_draw = None
    draw_vals = [d["avg_draw_w"] for d in daily if d.get("avg_draw_w")]
    if draw_vals:
        avg_draw = round(sum(draw_vals) / len(draw_vals), 1)

    est = analysis["estimates"]
    cap_est = est.get("capacity_based")
    cyc_est = est.get("cycle_based")
    prefer_cap = est.get("prefer_capacity", False)

    if prefer_cap and cap_est and cap_est.get("months_to_service"):
        lifespan_str = f"~{cap_est['months_to_service']} months (capacity trend)"
    elif cyc_est and cyc_est.get("months_to_rated"):
        lifespan_str = f"~{cyc_est['months_to_rated']} months (cycle rate)"
    else:
        lifespan_str = "Not enough data yet"

    # Build HTML — include time so same-day reports don't overwrite each other
    filename = f"battery_report_{end.strftime('%Y-%m-%d_%H%M')}.html"
    filepath = output_dir / filename

    signals_html = _render_signals(analysis["signals"])
    plugged_html = _render_plugged(analysis.get("plugged_pattern"))
    estimates_html = _render_estimates(cap_est, cyc_est, prefer_cap)

    html = _HTML_TEMPLATE.format(
        chartjs_script=_chartjs_script(),
        report_date=end.strftime("%B %d, %Y"),
        period_days=period_days,
        period_start=start.strftime("%b %d, %Y"),
        period_end=end.strftime("%b %d, %Y"),
        current_health=f"{current_health}%" if current_health else "—",
        current_cycles=current_cycles or "—",
        design_cap=design_cap or "—",
        max_cap=max_cap or "—",
        capacity_lost=f"{capacity_lost} mAh" if capacity_lost else "—",
        avg_draw=f"{avg_draw} W" if avg_draw else "—",
        lifespan_str=lifespan_str,
        daily_json=json.dumps(daily),
        timeline_json=json.dumps(timeline),
        habits_json=json.dumps(habits),
        signals_html=signals_html,
        plugged_html=plugged_html,
        estimates_html=estimates_html,
        disclaimer=DISCLAIMER,
        snapshot_count=len(snapshots),
        generated_at=end.strftime("%Y-%m-%d %H:%M UTC"),
    )

    filepath.write_text(html, encoding="utf-8")
    log_report(db_path, str(filepath), start.isoformat(), end.isoformat())
    return str(filepath)


def _render_signals(signals: list[dict]) -> str:
    if not signals:
        return ""
    items = ""
    for s in signals:
        icon = "⚠️" if s["level"] == "warning" else "💡"
        items += f'<div class="signal {s["level"]}"><span class="signal-icon">{icon}</span>{s["message"]}</div>\n'
    return f"""
<details class="signals-section" open>
  <summary>Health Signals <span class="badge">{len(signals)}</span></summary>
  <div class="signals-body">
    {items}
    <p class="disclaimer">⚠ {DISCLAIMER}</p>
  </div>
</details>"""


def _render_plugged(plugged: dict | None) -> str:
    if not plugged:
        return ""
    return f'<div class="info-banner">ℹ️ {plugged["message"]}</div>'


def _render_estimates(cap_est: dict | None, cyc_est: dict | None, prefer_cap: bool) -> str:
    parts = []
    if cap_est and cap_est.get("months_to_service"):
        trend = cap_est.get("slope_mah_per_day", 0)
        parts.append(
            f'<div class="estimate {"primary" if prefer_cap else "secondary"}">'
            f'<strong>Capacity trend estimate</strong>: ~{cap_est["months_to_service"]} months '
            f'until 80% design capacity (losing ~{abs(trend):.2f} mAh/day on average). '
            f'<em>This estimate is based on observed trends and is for reference only.</em></div>'
        )
    if cyc_est and cyc_est.get("months_to_rated"):
        parts.append(
            f'<div class="estimate {"primary" if not prefer_cap else "secondary"}">'
            f'<strong>Cycle-based estimate</strong>: ~{cyc_est["months_to_rated"]} months '
            f'until {cyc_est["rated_cycles"]} rated cycles '
            f'({cyc_est["current_cycles"]} now, ~{cyc_est["cycles_per_month"]}/month). '
            f'<em>This estimate is based on observed trends and is for reference only.</em></div>'
        )
    return "\n".join(parts)


# ── HTML template ─────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Battery Report — {report_date}</title>
{chartjs_script}
<style>
  :root {{
    --bg: #f8f9fa; --card: #ffffff; --text: #1a1a2e; --muted: #6c757d;
    --accent: #4361ee; --warning: #f4a261; --danger: #e63946;
    --good: #2dc653; --border: #dee2e6;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0d1117; --card: #161b22; --text: #e6edf3; --muted: #8b949e;
      --accent: #58a6ff; --border: #30363d;
    }}
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--bg); color: var(--text); padding: 24px; line-height: 1.6; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 28px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 28px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 18px 16px; text-align: center; }}
  .card-value {{ font-size: 1.8rem; font-weight: 700; color: var(--accent); }}
  .card-label {{ font-size: 0.78rem; color: var(--muted); margin-top: 4px; text-transform: uppercase; letter-spacing: .05em; }}
  .charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 20px; margin-bottom: 28px; }}
  .chart-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
  .chart-box h3 {{ font-size: 0.95rem; color: var(--muted); margin-bottom: 14px; }}
  .signals-section {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 16px 20px; margin-bottom: 20px; }}
  .signals-section summary {{ font-weight: 600; cursor: pointer; font-size: 1rem; }}
  .badge {{ background: var(--warning); color: #fff; border-radius: 999px;
    padding: 1px 8px; font-size: 0.75rem; margin-left: 8px; }}
  .signals-body {{ margin-top: 14px; display: flex; flex-direction: column; gap: 10px; }}
  .signal {{ padding: 12px 14px; border-radius: 8px; font-size: 0.88rem; display: flex; gap: 10px; align-items: flex-start; }}
  .signal.warning {{ background: #fff3cd; color: #856404; border-left: 3px solid var(--warning); }}
  .signal.caution {{ background: #d1ecf1; color: #0c5460; border-left: 3px solid var(--accent); }}
  .signal.info {{ background: #d4edda; color: #155724; border-left: 3px solid var(--good); }}
  @media (prefers-color-scheme: dark) {{
    .signal.warning {{ background: #3d2b00; color: #ffc107; }}
    .signal.caution {{ background: #003d4d; color: #7dd3fc; }}
    .signal.info {{ background: #003322; color: #4ade80; }}
  }}
  .signal-icon {{ font-size: 1.1rem; flex-shrink: 0; }}
  .disclaimer {{ font-size: 0.78rem; color: var(--muted); margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }}
  .estimate {{ padding: 12px 14px; border-radius: 8px; font-size: 0.88rem; background: var(--card);
    border: 1px solid var(--border); margin-bottom: 10px; }}
  .estimate.primary {{ border-left: 3px solid var(--accent); }}
  .estimate.secondary {{ border-left: 3px solid var(--border); opacity: 0.85; }}
  .info-banner {{ background: #e8f4fd; color: #1a6fa8; border-radius: 8px; padding: 12px 16px;
    font-size: 0.88rem; margin-bottom: 16px; border-left: 3px solid var(--accent); }}
  @media (prefers-color-scheme: dark) {{
    .info-banner {{ background: #003152; color: #7dd3fc; }}
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.83rem; }}
  th {{ text-align: left; padding: 8px 10px; border-bottom: 2px solid var(--border); color: var(--muted); }}
  td {{ padding: 7px 10px; border-bottom: 1px solid var(--border); }}
  .table-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 20px; margin-bottom: 20px; overflow-x: auto; }}
  footer {{ color: var(--muted); font-size: 0.78rem; text-align: center; margin-top: 20px; }}
</style>
</head>
<body>

<h1>🔋 Battery Report</h1>
<p class="subtitle">{period_start} → {period_end} &nbsp;·&nbsp; {snapshot_count} snapshots recorded</p>

<div class="cards">
  <div class="card"><div class="card-value">{current_health}</div><div class="card-label">Battery Health</div></div>
  <div class="card"><div class="card-value">{current_cycles}</div><div class="card-label">Cycle Count</div></div>
  <div class="card"><div class="card-value">{max_cap} mAh</div><div class="card-label">Current Max Cap.</div></div>
  <div class="card"><div class="card-value">{design_cap} mAh</div><div class="card-label">Design Capacity</div></div>
  <div class="card"><div class="card-value">{capacity_lost}</div><div class="card-label">Capacity Lost (period)</div></div>
  <div class="card"><div class="card-value">{avg_draw}</div><div class="card-label">Avg Power Draw</div></div>
</div>

{plugged_html}

<div class="charts">
  <div class="chart-box"><h3>Battery Health % Over Time</h3><canvas id="healthChart"></canvas></div>
  <div class="chart-box"><h3>Cycle Count Progression</h3><canvas id="cycleChart"></canvas></div>
  <div class="chart-box"><h3>Daily Avg Power Draw (W)</h3><canvas id="drawChart"></canvas></div>
  <div class="chart-box"><h3>Charge % Timeline</h3><canvas id="timelineChart"></canvas></div>
  <div class="chart-box"><h3>Charging Habits (discharge sessions)</h3><canvas id="habitsChart"></canvas></div>
</div>

{signals_html}

<div style="margin-bottom:20px">
  <h2 style="font-size:1rem;margin-bottom:12px">Life Estimates</h2>
  <p style="font-size:0.8rem;color:var(--muted);margin-bottom:10px">
    Estimated time until battery reaches Apple's 80% service threshold.
    Two methods are shown where data allows — capacity trend is more reliable if you rarely discharge the battery.
  </p>
  {estimates_html}
</div>

<div class="table-box">
  <h3 style="font-size:0.95rem;color:var(--muted);margin-bottom:12px">Daily Averages</h3>
  <table>
    <thead><tr>
      <th>Date</th><th>Health %</th><th>Cycles</th><th>Max Cap (mAh)</th>
      <th>Avg Draw (W)</th><th>Temp (°C)</th><th>Min/Max Charge %</th>
    </tr></thead>
    <tbody id="dailyTable"></tbody>
  </table>
</div>

<footer>Generated {generated_at} · BatteryJuice · {disclaimer}</footer>

<script>
const daily = {daily_json};
const timeline = {timeline_json};
const habits = {habits_json};

const labels = daily.map(d => d.date);
const C = (ctx, cfg) => new Chart(ctx, cfg);

// 1. Health %
C(document.getElementById('healthChart'), {{
  type: 'line',
  data: {{
    labels,
    datasets: [{{ label: 'Health %', data: daily.map(d => d.health_pct),
      borderColor: '#2dc653', backgroundColor: 'rgba(45,198,83,.1)', fill: true,
      tension: 0.3, pointRadius: 2 }}]
  }},
  options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ min: 70, max: 100 }} }} }}
}});

// 2. Cycles
C(document.getElementById('cycleChart'), {{
  type: 'line',
  data: {{
    labels,
    datasets: [{{ label: 'Cycles', data: daily.map(d => d.cycle_count),
      borderColor: '#4361ee', backgroundColor: 'rgba(67,97,238,.1)', fill: true,
      tension: 0.3, pointRadius: 2 }}]
  }},
  options: {{ plugins: {{ legend: {{ display: false }} }} }}
}});

// 3. Power draw
C(document.getElementById('drawChart'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [{{ label: 'Avg Draw (W)', data: daily.map(d => d.avg_draw_w),
      backgroundColor: 'rgba(244,162,97,.7)', borderRadius: 4 }}]
  }},
  options: {{ plugins: {{ legend: {{ display: false }} }} }}
}});

// 4. Charge timeline
const tlLabels = timeline.map(t => t.ts);
const tlData = timeline.map(t => t.pct);
const tlColors = timeline.map(t => t.charging ? 'rgba(45,198,83,0.7)' : 'rgba(67,97,238,0.5)');
C(document.getElementById('timelineChart'), {{
  type: 'bar',
  data: {{
    labels: tlLabels,
    datasets: [{{ label: 'Charge %', data: tlData, backgroundColor: tlColors, borderRadius: 2 }}]
  }},
  options: {{
    plugins: {{ legend: {{ display: false }},
      tooltip: {{ callbacks: {{ label: ctx => `${{ctx.raw}}% (${{timeline[ctx.dataIndex].charging ? 'charging' : 'discharging'}})` }} }} }},
    scales: {{ x: {{ display: false }}, y: {{ min: 0, max: 100 }} }}
  }}
}});

// 5. Habits donut
C(document.getElementById('habitsChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['Near 100% (high)', 'Normal (20–99%)', 'Below 10% (deep)'],
    datasets: [{{ data: [habits.full_pct, habits.normal_pct, habits.deep_pct],
      backgroundColor: ['#f4a261', '#2dc653', '#e63946'] }}]
  }},
  options: {{ plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});

// Daily table
const tbody = document.getElementById('dailyTable');
daily.forEach(d => {{
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td>${{d.date}}</td>
    <td>${{d.health_pct ?? '—'}}%</td>
    <td>${{d.cycle_count ?? '—'}}</td>
    <td>${{d.max_capacity_mah ?? '—'}}</td>
    <td>${{d.avg_draw_w ?? '—'}}</td>
    <td>${{d.avg_temp_c ?? '—'}}</td>
    <td>${{d.min_charge_pct ?? '—'}} / ${{d.max_charge_pct ?? '—'}}</td>`;
  tbody.appendChild(tr);
}});
</script>
</body>
</html>"""
