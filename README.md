# 🔋 BatteryJuice

> A lightweight macOS menu bar app that tracks your MacBook battery health over time and generates beautiful HTML reports.

![macOS](https://img.shields.io/badge/macOS-11%2B-blue?logo=apple)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **Real-time stats** in the menu bar — charge %, health, cycles, power draw, temperature
- **Background monitoring** every 30 minutes, auto-starts at login
- **HTML reports** generated automatically on your chosen interval (daily, weekly, …)
  - 5 interactive charts: health trend, cycle progression, power draw, charge timeline, charging habits
  - Summary cards with capacity lost, avg power draw, estimated remaining life
- **Advisory health signals** (soft warnings — never alarmist):
  - Capacity degradation rate & acceleration
  - Cycle burn rate
  - Power drain anomalies
  - High temperature while charging (immediate notification)
  - Charging habit patterns (frequent 100% charge, deep discharges)
- **Two life estimates**: capacity-trend based (reliable even if you rarely unplug) + cycle-based
- **Plugged-in mode detection** — warns when cycle count is unreliable and switches to capacity trend
- **Battery tips** — rotating actionable advice in the menu bar
- **CSV export** for your own analysis
- **Data retention** — auto-prunes old snapshots (configurable, default 1 year)

---

## Install

### Option 1 — Download .dmg (recommended, no Python needed)

Python is **not required** — the `.dmg` bundles everything.

1. Go to [**Releases**](../../releases/latest)
2. Download the `.dmg` for your Mac:
   - **Apple Silicon (M1/M2/M3/M4)** → `BatteryJuice-*-arm64.dmg`
   - **Intel Mac** → `BatteryJuice-*-x86_64.dmg`
3. Open the `.dmg`, drag **BatteryJuice.app** to `/Applications`
4. Double-click to launch — it appears in your menu bar as 🔋

> **First launch**: macOS may block the app since it's not notarized. Right-click → **Open** to bypass Gatekeeper.

---

### Option 2 — From source (developers)

**Requirements**: Python 3.8+, macOS 11+

```bash
git clone https://github.com/YOUR_USERNAME/BatteryJuice.git
cd BatteryJuice
./install.sh
```

The installer will:
- Install Python dependencies (`rumps`, `pyobjc-framework-Cocoa`)
- Set up `~/Library/Application Support/BatteryJuice/`
- Register a launchd agent (auto-starts at login)
- Take the first battery snapshot immediately

---

## Uninstall

```bash
./uninstall.sh
```

Removes the launchd agent. Optionally deletes all data and reports.

---

## Configuration

Edit `~/Library/Application Support/BatteryJuice/config.json`:

```json
{
  "report_interval_days": 7,
  "report_output_dir": "~/Library/Application Support/BatteryJuice/reports",
  "db_path": "~/Library/Application Support/BatteryJuice/battery.db",
  "data_retention_days": 365,
  "temp_alert_celsius": 45,
  "apple_rated_cycles": 1000
}
```

| Key | Default | Description |
|---|---|---|
| `report_interval_days` | `7` | Auto-generate report every N days (min: 1) |
| `data_retention_days` | `365` | Delete snapshots older than N days |
| `temp_alert_celsius` | `45` | Alert temperature threshold while charging |
| `apple_rated_cycles` | `1000` | Rated cycles for your MacBook model |

Open via **Preferences…** in the menu bar.

---

## Menu Bar

```
🔋 86% ⚡
├── Health: 84.6%  (7257 / 8579 mAh)
├── Cycles: 151  (~849 remaining, est. 28 mo)
├── Draw:   9.9 W
├── Temp:   30.8°C
├── Cap:    7257 / 8579 mAh
├── Est. life: ~28 mo (capacity trend)
├── ─────────────────────────
├── ⚠ Advisory (1 signal)        ← only shown when signals fire
│   └── • Capacity declining faster than usual
├── ─────────────────────────
├── 💡 Keep charge between 20–80% for best longevity   ← click to rotate
├── ─────────────────────────
├── Generate Report
├── Past Reports ▶
├── Export CSV…
├── Open Reports Folder
├── ─────────────────────────
├── Preferences…
└── Quit
```

---

## Battery Tips

The app surfaces actionable tips in the menu bar (rotating, click to see next):

- Keep charge between **20–80%** for best long-term capacity
- Enable **Optimized Battery Charging** in System Settings → Battery
- Avoid sustained **high temperatures** (especially while charging)
- High power draw drains cycles faster — use **Low Power Mode** on battery
- Occasional full discharge (once/month) helps calibrate the charge gauge
- Store your Mac at **~50% charge** if unused for weeks
- Short top-ups are gentler than deep discharges
- Check battery health anytime in **System Settings → Battery → Battery Health**

---

## Advisory Signals

All signals are **observations for reference only** — not diagnoses.

| Signal | What it means |
|---|---|
| Capacity loss rate | Battery losing >1–2 mAh/day on average |
| Health acceleration | Capacity losing faster than previous month |
| Cycle burn rate | Accumulating >30–50 cycles/month |
| Drain anomaly | Power draw >20–40% above your 30-day baseline |
| High temp while charging | Temperature >45°C while plugged in |
| Frequent full charges | Often discharging from 100% |
| Deep discharge | Frequently charging from below 10% |

> For authoritative battery assessment, use **Apple Diagnostics** (hold D at startup) or visit an **Apple Store**.

---

## Life Estimates

Two independent methods are shown when data allows (≥14 days):

**Capacity-trend estimate** — fits a regression line on recorded max capacity over time and projects when it reaches Apple's 80% service threshold. Works even if you rarely unplug.

**Cycle-based estimate** — projects months until your rated cycle count based on observed cycle accumulation rate. Less reliable for always-plugged users.

The app automatically highlights the more reliable estimate for your usage pattern.

---

## Build from Source (for contributors)

```bash
pip install -r requirements-build.txt
pyinstaller batteryjuice.spec --clean --noconfirm

# Package into .dmg
hdiutil create \
  -volname "BatteryJuice" \
  -srcfolder "dist/BatteryJuice.app" \
  -ov -format UDZO \
  dist/BatteryJuice.dmg
```

Releases are built automatically via GitHub Actions on tag push:
```bash
git tag v1.0.0 && git push --tags
```

---

## Privacy

Battery Monitor runs **entirely locally**. No data leaves your machine. All battery data is stored in SQLite at `~/Library/Application Support/BatteryJuice/battery.db`.

---

## Contributing

Issues and PRs are welcome. Please open an issue before starting large changes.

---

## License

MIT © 2024
