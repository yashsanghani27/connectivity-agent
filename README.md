# Connectivity Agent

A desktop application for testing LTE sensor signal quality at client sites, built for Henkel / LOCTITE Pulse field engineers.

---

## What it does

The app connects to a Loctite Pulse sensor's HTTP endpoint (`/current`), collects signal quality readings in real time, displays them on a live dashboard, and exports a formatted Excel report — replacing manual monitoring during site visits.

No browser automation. No msedgedriver. No Selenium. The sensor already exposes a JSON API; the app calls it directly with Python's built-in HTTP client.

---

## Features

- **1 or 2 sensor mode** — switch at any time with the ⇄ button
- **Live dashboard** — RSRP, RSRQ, Upload Speed, coloured by grade (Excellent / Good / Moderate / Poor)
- **Auto-stop** — test ends after 5 clean samples per sensor or 2:30 elapsed
- **Disconnect debounce** — brief dropouts during the sensor's upload speed test (~12 s) are silently ignored; the GUI never flickers
- **Speed deduplication** — duplicate Upload Speed readings from the same upload cycle are filtered out before being stored
- **Step-by-step instruction panel** — guides the field engineer through the test with a live progress indicator
- **Results panel** — shows final grade, speeds, and sample count after each test
- **Excel report** — single `data` tab; each sensor block starts with a `Sensor N - MCCMNC network` header row; two empty rows separate sensors

---

## Project structure

```
ConnectivityAgent/
├── main.py                    # Entry point — calls launch()
├── requirements.txt           # openpyxl, Pillow
├── build.bat                  # PyInstaller → dist/ConnectivityAgent.exe
├── assets/
│   ├── LP_app.ico
│   └── Loc_Pulse.png
├── core/
│   ├── __init__.py
│   ├── sensor_client.py       # HTTP GET /current → SensorReading dataclass
│   ├── measurement.py         # SensorStore, Sample, grading functions
│   └── report.py              # build_report_bytes() → BytesIO → .xlsx
└── gui/
    ├── __init__.py
    └── main_window.py         # ConnectivityAgentApp — full tkinter dashboard
```

---

## Requirements

- Python 3.10 or later
- Internet access to reach `agent.electricimp.com` (the sensor's cloud endpoint)

```bash
pip install openpyxl Pillow
```

---

## Run locally

```bash
python main.py
```

---

## Build a standalone .exe

```bash
pip install pyinstaller openpyxl Pillow
build.bat
```

Output: `dist\ConnectivityAgent.exe` — a single file, no Python needed on the target machine.

---

## How the sensor connection works

The Loctite Pulse sensor page is a jQuery single-page app. Its JavaScript calls:

```
GET https://agent.electricimp.com/<token>/current
```

which returns a JSON object with all signal metrics (`RSRP`, `RSRQ`, `SINR`, `speed1`, `MCCMNC`, etc.).

The app calls the same endpoint directly — no browser, no XPath scraping. One HTTP request per second per sensor acts as the natural rate limiter.

**Connection rule:** a reading is only treated as "Connected" if the sensor's `state` field equals `"connected"` AND at least one of `RSRP` / `RSRQ` is a real negative number. The sensor page returns placeholder zeros when the radio hasn't registered on the network yet; those are rejected.

---

## Signal quality scoring

| Metric | Excellent | Good | Moderate | Poor |
|---|---|---|---|---|
| Upload Speed (kB/s) | ≥ 100 | ≥ 80 | ≥ 50 | < 50 |
| RSRP (dBm) | ≥ −80 | ≥ −95 | ≥ −110 | < −110 |
| RSRQ (dB) | ≥ −10 | ≥ −15 | ≥ −20 | < −20 |

**Per-sample grade** — Upload Speed alone (column `Signal Quality` in the Excel file).

**FINAL SIGNAL QUALITY** — mean of the top-3 Upload Speeds across all samples. Rewards the best conditions achieved at that location without being thrown off by a slow early reading.

**OVERALL SIGNAL QUALITY** — mean of all Upload Speeds. More conservative; useful for cross-site comparison.

---

## Excel report layout

```
Sensor 1 - 26203 network          ← yellow header row
[Column headers]                   ← navy background
[Data rows — samples + events]     ← alternating shading
[blank]
OVERALL RESULT — Sensor 1: GOOD signal (99.9 kB/s, top-3 of 5)
FINAL SIGNAL QUALITY               → Good
OVERALL SIGNAL QUALITY             → Good
[blank]
[blank]                            ← two empty rows between sensors
Sensor 2 - 26202 network
...
```

**Plant + Building append rule:** saving with the same Plant name and Building/Floor/Section appends a new session block to the existing file. A different plant or building opens a fresh Save-As dialog.

---

## Authors

Created with ♥ by AE — Henkel Application Engineering
