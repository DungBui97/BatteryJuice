"""
BatteryJuice — macOS Menu Bar App
Entry point. Run with: python3 src/app.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import rumps

# ── resolve paths ─────────────────────────────────────────────────────────────

def _support_dir() -> Path:
    """~/Library/Application Support/BatteryJuice"""
    p = Path.home() / "Library" / "Application Support" / "BatteryJuice"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _bundled_config() -> Path:
    """config.json next to this script (or bundled app)."""
    return Path(__file__).parent.parent / "config.json"


def _load_config() -> dict:
    support = _support_dir()
    user_cfg = support / "config.json"
    if not user_cfg.exists():
        src = _bundled_config()
        if src.exists():
            import shutil
            shutil.copy(src, user_cfg)
        else:
            user_cfg.write_text(json.dumps(_DEFAULT_CONFIG, indent=2))
    with open(user_cfg) as f:
        cfg = json.load(f)
    # fill in any missing keys from defaults
    for k, v in _DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


_DEFAULT_CONFIG = {
    "report_interval_days": 7,
    "report_output_dir": str(Path.home() / "Library" / "Application Support" / "BatteryJuice" / "reports"),
    "db_path": str(Path.home() / "Library" / "Application Support" / "BatteryJuice" / "battery.db"),
    "data_retention_days": 365,
    "temp_alert_celsius": 45,
    "apple_rated_cycles": 1000,
}

# add src/ to path so imports work when run directly
sys.path.insert(0, str(Path(__file__).parent))

from collector import collect
from database import (
    init_db, insert_snapshot, get_latest_snapshot,
    get_reports, get_last_report_time, prune_old_snapshots, export_csv,
)
from analyzer import analyze
from reporter import generate_report


# ── tips shown in menu and report ─────────────────────────────────────────────

BATTERY_TIPS = [
    "Keep charge between 20–80% for best long-term capacity.",
    "Avoid leaving your Mac plugged in at 100% for extended periods.",
    "High temperatures degrade battery faster — avoid using on soft surfaces.",
    "Enable 'Optimized Battery Charging' in System Settings → Battery.",
    "Occasional full discharge (once/month) helps calibrate the gauge.",
    "Dim the screen and close unused apps to reduce power draw.",
    "Use Low Power Mode when on battery to extend charge cycles.",
    "Store your Mac at ~50% charge if not using it for weeks.",
    "macOS shows battery health in System Settings → Battery → Battery Health.",
    "Frequent short top-ups are gentler on the battery than deep discharges.",
]


class BatteryJuiceApp(rumps.App):
    def __init__(self):
        super().__init__("🔋", quit_button=None)
        self.config = _load_config()
        self._db = self.config["db_path"]
        init_db(self._db)

        self._last_temp_alert: float = 0.0   # epoch, throttle 1/hr
        self._analysis: dict = {}
        self._latest: dict | None = None
        self._tip_index: int = 0
        self._pending_past_reports_rebuild: bool = False

        # Build initial menu
        self._build_menu()

        # Timers
        rumps.Timer(self._tick_live, 60).start()       # live stats every 60s
        rumps.Timer(self._tick_collect, 1800).start()  # collect every 30 min

        # Run immediately in background
        threading.Thread(target=self._initial_collect, daemon=True).start()

    # ── initial load ──────────────────────────────────────────────────────────

    def _initial_collect(self):
        time.sleep(2)  # let the menu bar render first
        self._do_collect()
        # Do NOT call _refresh_menu() here — this runs on a background thread.
        # _tick_live fires within 60s on the main thread and will update the UI.

    # ── timer callbacks ───────────────────────────────────────────────────────

    def _tick_live(self, _):
        # Runs on the main thread (rumps NSTimer). Must NOT block — no subprocess calls.
        # Spawn a background thread to collect fresh data; UI updates from self._latest.
        threading.Thread(target=self._do_live_collect, daemon=True).start()
        # Refresh UI from latest known data (non-blocking).
        self._refresh_stats_items()
        self._refresh_advisory()
        self._tip_index = (self._tip_index + 1) % len(BATTERY_TIPS)
        self._item_tip.title = f"💡 {BATTERY_TIPS[self._tip_index]}"
        # Handle deferred UI rebuilds requested from background threads.
        if self._pending_past_reports_rebuild:
            self._pending_past_reports_rebuild = False
            self._rebuild_past_reports()

    def _do_live_collect(self):
        """Lightweight live collect — updates self._latest only, no DB write."""
        live = collect()
        if live:
            self._latest = live

    def _tick_collect(self, _):
        threading.Thread(target=self._do_collect, daemon=True).start()

    # ── core collect + analysis ───────────────────────────────────────────────

    def _do_collect(self):
        # WARNING: runs on a background thread. Must NOT touch any UI (NSMenuItem).
        # Only update self._latest and self._analysis (simple assignments, GIL-safe).
        data = collect()
        if not data:
            return
        insert_snapshot(self._db, data)
        prune_old_snapshots(self._db, self.config["data_retention_days"])
        self._latest = data

        # temp alert — rumps.notification is thread-safe
        if data.get("is_charging") and data.get("temperature_c", 0) >= self.config["temp_alert_celsius"]:
            now = time.time()
            if now - self._last_temp_alert > 3600:
                self._last_temp_alert = now
                temp = data["temperature_c"]
                rumps.notification(
                    "BatteryJuice — Heat Advisory",
                    f"Temperature: {temp}°C while charging",
                    "High heat during charging may affect long-term capacity. Consider removing the case or moving to a cooler surface.",
                )

        # run analysis (pure Python, no UI)
        from database import get_snapshots
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=90)
        snapshots = get_snapshots(self._db, start.isoformat(), end.isoformat())
        self._analysis = analyze(snapshots, self.config, data)

        # check if auto report is due (file I/O only, no UI)
        self._check_auto_report()
        # UI refresh happens on next _tick_live (within 60s, main thread)

    def _check_auto_report(self):
        interval = self.config["report_interval_days"]
        last = get_last_report_time(self._db)
        if last:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - last_dt).days < interval:
                return
        else:
            # No prior report — only generate once we have at least 7 days of data,
            # otherwise the first-run report is nearly empty and not useful.
            from database import get_snapshots as _gs
            start = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            end = datetime.now(timezone.utc).isoformat()
            if len(_gs(self._db, start, end)) < 2:
                return
        # generate
        try:
            path = generate_report(
                self._db,
                self.config["report_output_dir"],
                interval,
                self.config,
            )
            signals = self._analysis.get("signals", [])
            body = f"Report saved. {len(signals)} advisory signal(s)." if signals else "Report saved."
            rumps.notification("BatteryJuice", "Scheduled Report Ready", body)
        except Exception as e:
            rumps.notification("BatteryJuice", "Report Error", str(e))

    # ── menu building ─────────────────────────────────────────────────────────

    def _build_menu(self):
        self.menu.clear()

        self._item_health    = rumps.MenuItem("Health: —")
        self._item_cycles    = rumps.MenuItem("Cycles: —")
        self._item_draw      = rumps.MenuItem("Draw: —")
        self._item_temp      = rumps.MenuItem("Temp: —")
        self._item_cap       = rumps.MenuItem("Cap: —")
        self._item_est       = rumps.MenuItem("Est. life: —")
        self._item_advisory  = rumps.MenuItem("⚠ Advisory")
        self._item_tip       = rumps.MenuItem("💡 Tip: loading…")

        self.menu = [
            self._item_health,
            self._item_cycles,
            self._item_draw,
            self._item_temp,
            self._item_cap,
            self._item_est,
            None,  # separator
            self._item_advisory,
            None,
            self._item_tip,
            None,
            rumps.MenuItem("Generate Report", callback=self._on_generate_report),
            self._past_reports_menu(),
            rumps.MenuItem("Export CSV…", callback=self._on_export_csv),
            rumps.MenuItem("Open Reports Folder", callback=self._on_open_reports),
            None,
            rumps.MenuItem("Preferences…", callback=self._on_preferences),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

        self._item_advisory.set_callback(None)
        self._tip_index = int(time.time()) % len(BATTERY_TIPS)
        self._item_tip.title = f"💡 {BATTERY_TIPS[self._tip_index]}"
        self._item_tip.set_callback(self._on_next_tip)

    def _past_reports_menu(self) -> rumps.MenuItem:
        sub = rumps.MenuItem("Past Reports")
        self._past_report_keys = []
        reports = get_reports(self._db, limit=10)
        if not reports:
            key = "No reports yet"
            sub.add(rumps.MenuItem(key))
            self._past_report_keys.append(key)
        else:
            for r in reports:
                # Use date+time to guarantee unique keys for same-day reports.
                label = r["generated_at"][:16].replace("T", " ")
                key = f"Report {label}"
                item = rumps.MenuItem(key, callback=self._open_report)
                item._filepath = r["filepath"]
                sub.add(item)
                self._past_report_keys.append(key)
        return sub

    def _refresh_stats_items(self):
        if not self._latest:
            return
        d = self._latest
        design = d.get("design_capacity_mah")
        max_cap = d.get("max_capacity_mah")
        health = round(max_cap / design * 100, 1) if (design and max_cap) else "—"
        cap_str = f"{max_cap or '—'} / {design or '—'} mAh"
        pct = d.get("current_pct", "—")
        charging = " ⚡" if d.get("is_charging") else ""

        self.title = f"🔋 {pct}%{charging}"
        self._item_health.title = f"Health: {health}{'%' if health != '—' else ''}  ({cap_str})"
        self._item_cycles.title = f"Cycles: {d.get('cycle_count', '—')}{self._cycle_suffix()}"
        self._item_draw.title   = f"Draw: {d.get('power_draw_w', '—')} W"
        self._item_temp.title   = f"Temp: {d.get('temperature_c', '—')}°C"
        self._item_cap.title    = f"Cap: {cap_str}"
        self._item_est.title    = f"Est. life: {self._lifespan_str()}"

    def _cycle_suffix(self) -> str:
        est = self._analysis.get("estimates", {}).get("cycle_based")
        if est and est.get("months_to_rated"):
            return f"  (~{est['months_to_rated']} mo to rated)"
        return ""

    def _lifespan_str(self) -> str:
        est = self._analysis.get("estimates", {})
        cap = est.get("capacity_based")
        cyc = est.get("cycle_based")
        prefer = est.get("prefer_capacity", False)
        if prefer and cap and cap.get("months_to_service"):
            return f"~{cap['months_to_service']} mo (capacity trend)"
        if cyc and cyc.get("months_to_rated"):
            return f"~{cyc['months_to_rated']} mo (cycle rate)"
        return "Not enough data yet"

    def _refresh_advisory(self):
        signals = self._analysis.get("signals", [])
        real_signals = [s for s in signals if not s.get("immediate")]
        if not real_signals:
            self._item_advisory.title = ""
            return
        self._item_advisory.title = f"⚠ Advisory ({len(real_signals)} signal{'s' if len(real_signals) > 1 else ''})"
        # Clear existing sub-items tracked in _advisory_keys, then re-add.
        # Avoids touching private rumps/_AppKit internals.
        for key in getattr(self, "_advisory_keys", []):
            try:
                del self._item_advisory[key]
            except KeyError:
                pass
        self._advisory_keys = []
        for s in real_signals[:5]:
            icon = "⚠" if s["level"] == "warning" else "•"
            label = s["message"]
            if len(label) > 70:
                label = label[:70] + "…"
            title = f"{icon} {label}"
            self._item_advisory.add(rumps.MenuItem(title))
            self._advisory_keys.append(title)

    # ── menu actions ──────────────────────────────────────────────────────────

    @rumps.clicked("Generate Report")
    def _on_generate_report(self, _):
        def _run():
            try:
                path = generate_report(
                    self._db,
                    self.config["report_output_dir"],
                    self.config["report_interval_days"],
                    self.config,
                )
                subprocess.run(["open", path])
                # Signal main thread to rebuild Past Reports on next _tick_live.
                # Cannot call _rebuild_past_reports() here — background thread.
                self._pending_past_reports_rebuild = True
            except Exception as e:
                rumps.notification("BatteryJuice", "Report Error", str(e))
        threading.Thread(target=_run, daemon=True).start()

    def _open_report(self, sender):
        path = getattr(sender, "_filepath", None)
        if path and Path(path).exists():
            subprocess.run(["open", path])

    @rumps.clicked("Export CSV…")
    def _on_export_csv(self, _):
        out = Path(self.config["report_output_dir"]).expanduser() / f"battery_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        count = export_csv(self._db, str(out))
        if count:
            subprocess.run(["open", "-R", str(out)])
            rumps.notification("BatteryJuice", "CSV Exported", f"{count} rows saved to {out.name}")
        else:
            rumps.notification("BatteryJuice", "Export", "No data to export yet.")

    @rumps.clicked("Open Reports Folder")
    def _on_open_reports(self, _):
        folder = Path(self.config["report_output_dir"]).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(folder)])

    @rumps.clicked("Preferences…")
    def _on_preferences(self, _):
        cfg_path = _support_dir() / "config.json"
        subprocess.run(["open", str(cfg_path)])

    def _on_next_tip(self, _):
        self._tip_index = (self._tip_index + 1) % len(BATTERY_TIPS)
        self._item_tip.title = f"💡 {BATTERY_TIPS[self._tip_index]}"

    def _rebuild_past_reports(self):
        try:
            past_item = self.menu["Past Reports"]
        except KeyError:
            return
        # Clear tracked sub-items by key, then re-add (avoids private AppKit internals).
        for key in getattr(self, "_past_report_keys", []):
            try:
                del past_item[key]
            except KeyError:
                pass
        self._past_report_keys = []
        reports = get_reports(self._db, limit=10)
        if not reports:
            key = "No reports yet"
            past_item.add(rumps.MenuItem(key))
            self._past_report_keys.append(key)
        else:
            for r in reports:
                label = r["generated_at"][:16].replace("T", " ")
                key = f"Report {label}"
                item = rumps.MenuItem(key, callback=self._open_report)
                item._filepath = r["filepath"]
                past_item.add(item)
                self._past_report_keys.append(key)


if __name__ == "__main__":
    BatteryJuiceApp().run()
