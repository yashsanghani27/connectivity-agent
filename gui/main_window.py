"""
Connectivity Agent — Desktop GUI

Plain HTTP polling to the sensor's /current JSON endpoint.
No Selenium, no browser, no msedgedriver. Works on any Windows
machine that has Python installed (or as a .exe from PyInstaller).
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Optional

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

from core.sensor_client import poll_sensor
from core.measurement import (
    SensorStore, Sample,
    grade_speed, grade_rsrp, grade_rsrq,
    MAX_MEASUREMENTS, MAX_DURATION_SECS,
)
from core.report import build_report_bytes, default_filename


# ── Design tokens ────────────────────────────────────────────────────
COLOR_BG      = "#F0F4F8"
COLOR_PANEL   = "#FFFFFF"
COLOR_HEADER  = "#E1000F"    # LOCTITE red
COLOR_DARK    = "#1A2B4A"    # navy
COLOR_TEXT    = "#1A2B4A"
COLOR_MUTED   = "#6B7A99"
COLOR_BORDER  = "#D1D9E6"
COLOR_CONN    = "#22C55E"
COLOR_DISCONN = "#EF4444"
COLOR_WAIT    = "#F59E0B"

PASTEL: Dict[str, tuple] = {
    "Excellent": ("#DCFCE7", "#166534"),
    "Good":      ("#DCFCE7", "#166534"),
    "Moderate":  ("#FEF9C3", "#92400E"),
    "Poor":      ("#FEE2E2", "#991B1B"),
    "--":        ("#D1D9E6", "#1A2B4A"),
}
GRADE_SOLID: Dict[str, str] = {
    "Excellent": "#22C55E", "Good": "#22C55E",
    "Moderate":  "#F59E0B", "Poor": "#EF4444",
    "--":        "#D1D9E6",
}
FONT      = "Segoe UI"
APP_TITLE = "Connectivity Agent"


def _fmt(secs: float) -> str:
    m, s = divmod(int(max(0, secs)), 60)
    return f"{m:02d}:{s:02d}"


def _btn(parent, text, cmd, bg, width=None, state="normal"):
    kw = dict(text=text, command=cmd, bg=bg, fg="white",
              activebackground=bg, font=(FONT, 10, "bold"),
              relief="flat", cursor="hand2", state=state, pady=7, padx=12)
    if width:
        kw["width"] = width
    return tk.Button(parent, **kw)


def _assets_dir() -> str:
    """Return the assets/ path whether running from source or as a .exe."""
    if getattr(sys, "frozen", False):
        # PyInstaller unpacks to a temp folder in sys._MEIPASS
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets")


class ConnectivityAgentApp:
    """
    Main application window.

    Thread model:
      * One background thread per sensor polls the HTTP endpoint every 1 s.
      * Results go into a queue.Queue.
      * master.after(100, _drain_queue) pulls results onto the Tk main
        thread and updates widgets — zero tk calls from background threads.
    """

    def __init__(self, master: tk.Tk, num_sensors: int = 1):
        self.master      = master
        self.num_sensors = num_sensors
        self.master.title(APP_TITLE)
        self.master.configure(bg=COLOR_BG)
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)

        if num_sensors == 2:
            self.master.geometry("1200x820")
            self.master.minsize(1100, 640)
        else:
            self.master.geometry("1000x780")
            self.master.minsize(800, 620)

        # ── Runtime state ───────────────────────────────────
        self.running      = False
        self.test_start: Optional[datetime] = None
        self._destroying  = False

        self.stores: Dict[int, SensorStore] = {
            n: SensorStore() for n in range(1, num_sensors + 1)}

        self._stop_events: Dict[int, threading.Event] = {
            n: threading.Event() for n in range(1, num_sensors + 1)}

        self.result_q: queue.Queue = queue.Queue()

        self._connected:  Dict[int, Optional[bool]] = {n: None  for n in range(1, num_sensors + 1)}
        self._first_conn: Dict[int, bool]           = {n: False for n in range(1, num_sensors + 1)}
        self._last_rec:   Dict[int, Optional[dict]] = {n: None  for n in range(1, num_sensors + 1)}

        # Disconnect debounce — the sensor briefly reports "disconnected"
        # while it performs an upload speed test (~10-12 s). Without debounce
        # this floods the report with Disconnected/Reconnected pairs that
        # are not real connectivity losses.
        #
        # How it works:
        #   - When a disconnect is detected, we DO NOT immediately log it.
        #     Instead we record when it started in _disc_since[n].
        #   - On every subsequent poll we check: has it been disconnected
        #     for >= DISC_DEBOUNCE_SECS? If yes, commit the event to the store.
        #   - When a reconnect is detected:
        #       • If the disconnect was never committed (too brief) → suppress both events.
        #       • If it WAS committed → log the Reconnected event.
        DISC_DEBOUNCE_SECS = 15   # disconnects shorter than this are suppressed
        self._DISC_DEBOUNCE  = DISC_DEBOUNCE_SECS
        self._disc_since:     Dict[int, Optional[datetime]] = {n: None  for n in range(1, num_sensors + 1)}
        self._disc_committed: Dict[int, bool]               = {n: False for n in range(1, num_sensors + 1)}

        self._session_saved   = False
        self._report_context: Optional[tuple] = None   # (plant, building, path)

        # ── Form variables ──────────────────────────────────
        self.plant_var    = tk.StringVar()
        self.building_var = tk.StringVar()
        self.asset_var    = tk.StringVar()
        self.url_vars     = {n: tk.StringVar() for n in range(1, num_sensors + 1)}

        # ── Widget references ───────────────────────────────
        self.status_labels:  Dict[int, tk.Label] = {}
        self.count_labels:   Dict[int, tk.Label] = {}
        self.metric_labels:  Dict[int, Dict[str, tk.Label]] = {n: {} for n in range(1, num_sensors + 1)}
        self.info_labels:    Dict[int, Dict[str, tk.Label]] = {n: {} for n in range(1, num_sensors + 1)}
        self.quality_labels: Dict[int, tk.Label] = {}

        self._build_ui()
        self._drain_queue()     # start queue pump

    # ─────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_action_bar()    # packed to bottom BEFORE the scroll body
        self._build_scroll_body()

    def _build_header(self):
        hdr = tk.Frame(self.master, bg=COLOR_HEADER, height=60)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=APP_TITLE,
                 font=(FONT, 18, "bold"), bg=COLOR_HEADER, fg="white"
                 ).pack(side="left", padx=20)

        # Try PNG logo; fall back to text branding
        logo_path = os.path.join(_assets_dir(), "Loc_Pulse.png")
        if _HAS_PIL and os.path.exists(logo_path):
            try:
                img = Image.open(logo_path)
                img.thumbnail((170, 46))
                self._logo_img = ImageTk.PhotoImage(img)
                tk.Label(hdr, image=self._logo_img, bg=COLOR_HEADER
                         ).pack(side="right", padx=20)
                return
            except Exception:
                pass
        # Text fallback
        tk.Label(hdr, text="LOCTITE.Pulse",
                 font=(FONT, 18, "bold italic"), bg=COLOR_HEADER, fg="white"
                 ).pack(side="right", padx=20)

    def _build_action_bar(self):
        bar = tk.Frame(self.master, bg=COLOR_PANEL,
                       highlightbackground=COLOR_BORDER, highlightthickness=1)
        bar.pack(fill="x", side="bottom")

        left = tk.Frame(bar, bg=COLOR_PANEL)
        left.pack(side="left", padx=14, pady=9)

        self.run_btn = _btn(left, "▶  Run Check", self.start_test,
                            COLOR_HEADER, width=14)
        self.run_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = _btn(left, "■  Stop", self.stop_test,
                             "#374151", state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 6))

        _btn(left, "↺  Reset", self.reset_test, COLOR_MUTED
             ).pack(side="left", padx=(0, 6))

        other = 2 if self.num_sensors == 1 else 1
        _btn(left, f"⇄  {other} Sensor{'s' if other > 1 else ''}",
             lambda o=other: self._switch_mode(o),
             COLOR_MUTED, width=12
             ).pack(side="left", padx=(0, 6))

        right = tk.Frame(bar, bg=COLOR_PANEL)
        right.pack(side="right", padx=14, pady=9)
        tk.Label(right, text="Created with ♥ by AE",
                 font=(FONT, 8), bg=COLOR_PANEL, fg=COLOR_MUTED
                 ).pack(side="right", padx=8)
        _btn(right, "⬇  Save Report", self.save_report,
             COLOR_DARK, width=14).pack(side="right")

    def _build_scroll_body(self):
        canvas = tk.Canvas(self.master, bg=COLOR_BG, highlightthickness=0)
        vsb    = ttk.Scrollbar(self.master, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body   = tk.Frame(canvas, bg=COLOR_BG)
        win_id = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win_id, width=e.width))
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        PAD = dict(padx=14, pady=6)
        self._build_config_card(body, PAD)
        self._build_status_strip(body, PAD)
        self._build_sensor_panels(body, PAD)

    def _build_config_card(self, body, PAD):
        card = tk.Frame(body, bg=COLOR_PANEL,
                        highlightbackground=COLOR_BORDER, highlightthickness=1)
        card.pack(fill="x", **PAD)

        def lbl(r, c, text):
            tk.Label(card, text=text, bg=COLOR_PANEL, fg=COLOR_MUTED,
                     font=(FONT, 9, "bold")).grid(
                row=r, column=c, sticky="w", padx=(12, 4), pady=9)

        def entry(r, c, var, span=1):
            tk.Entry(card, textvariable=var, font=(FONT, 10),
                     relief="solid", bd=1).grid(
                row=r, column=c, sticky="ew",
                padx=(0, 12), pady=9, columnspan=span)

        if self.num_sensors == 1:
            lbl(0, 0, "Sensor URL:")
            entry(0, 1, self.url_vars[1], span=5)
        else:
            lbl(0, 0, "Sensor 1 URL:")
            entry(0, 1, self.url_vars[1])
            lbl(0, 2, "Sensor 2 URL:")
            entry(0, 3, self.url_vars[2], span=3)

        lbl(1, 0, "Survey Plant:")
        entry(1, 1, self.plant_var)
        lbl(1, 2, "Building / Floor / Section:")
        entry(1, 3, self.building_var)
        lbl(1, 4, "Asset Ref.:")
        entry(1, 5, self.asset_var)

        for c in range(6):
            card.columnconfigure(c, weight=(1 if c % 2 == 1 else 0))

    def _build_status_strip(self, body, PAD):
        strip = tk.Frame(body, bg=COLOR_PANEL,
                         highlightbackground=COLOR_BORDER, highlightthickness=1)
        strip.pack(fill="x", padx=PAD["padx"], pady=(0, PAD["pady"]))
        inner = tk.Frame(strip, bg=COLOR_PANEL)
        inner.pack(side="left", padx=12, pady=8)
        col = 0

        def field(label, width, bg, init="--"):
            nonlocal col
            tk.Label(inner, text=label, bg=COLOR_PANEL, fg=COLOR_MUTED,
                     font=(FONT, 9, "bold")).grid(
                row=0, column=col, sticky="w", padx=(0, 4))
            w = tk.Label(inner, text=init, width=width, bg=bg,
                         fg=COLOR_TEXT, font=(FONT, 9, "bold"), relief="flat")
            w.grid(row=0, column=col + 1, padx=(0, 20))
            col += 2
            return w

        for n in range(1, self.num_sensors + 1):
            pre = f"S{n} " if self.num_sensors > 1 else ""
            self.status_labels[n] = field(f"{pre}Status:", 16, COLOR_BORDER, "Idle")
            self.count_labels[n]  = field(f"{pre}Meas.:", 9, "#FEF9C3",
                                          f"0 / {MAX_MEASUREMENTS}")

        self.timer_label   = field("Time Left:", 8, "#FEF9C3", _fmt(MAX_DURATION_SECS))
        self.elapsed_label = field("Elapsed:", 8, COLOR_BORDER, "00:00")

    def _build_sensor_panels(self, body, PAD):
        outer = tk.Frame(body, bg=COLOR_BG)
        outer.pack(fill="both", expand=True, **PAD)
        for i in range(self.num_sensors):
            outer.columnconfigure(i, weight=1)
            self._build_one_panel(outer, i + 1, col=i)

    def _build_one_panel(self, parent, n: int, col: int):
        title = f"Sensor {n}" if self.num_sensors > 1 else "Live Signal Readings"
        frame = tk.Frame(parent, bg=COLOR_PANEL,
                         highlightbackground=COLOR_BORDER, highlightthickness=1)
        frame.grid(row=0, column=col, sticky="nsew",
                   padx=(0, 6) if col == 0 else (6, 0), pady=2)

        # Panel header (navy bar)
        ph = tk.Frame(frame, bg=COLOR_DARK)
        ph.pack(fill="x")
        tk.Label(ph, text=title, font=(FONT, 12, "bold"),
                 bg=COLOR_DARK, fg="white").pack(side="left", padx=14, pady=9)

        # Panel body — three columns: metrics | sep | info | sep | quality
        body = tk.Frame(frame, bg=COLOR_PANEL)
        body.pack(fill="both", expand=True, padx=14, pady=12)
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=0)
        body.columnconfigure(2, weight=2)
        body.columnconfigure(3, weight=0)
        body.columnconfigure(4, weight=1)

        # ── Signal metrics ──────────────────────────────────
        ml = tk.Frame(body, bg=COLOR_PANEL)
        ml.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ml.columnconfigure(1, weight=1)

        tk.Label(ml, text="METRIC", font=(FONT, 8, "bold"),
                 bg=COLOR_PANEL, fg=COLOR_MUTED).grid(row=0, column=0, sticky="w")
        tk.Label(ml, text="VALUE", font=(FONT, 8, "bold"),
                 bg=COLOR_PANEL, fg=COLOR_MUTED).grid(row=0, column=1)
        ttk.Separator(ml, orient="horizontal").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        for i, (metric, unit) in enumerate(
                [("RSRP", "dBm"), ("RSRQ", "dB"), ("Speed", "kB/s")]):
            tk.Label(ml, text=f"{metric}  ({unit})",
                     font=(FONT, 10), bg=COLOR_PANEL, fg=COLOR_TEXT,
                     anchor="w").grid(row=i + 2, column=0, sticky="w", pady=5)
            lbl = tk.Label(ml, text="--", width=12,
                           bg=COLOR_BORDER, fg=COLOR_TEXT,
                           font=(FONT, 13, "bold"), anchor="center")
            lbl.grid(row=i + 2, column=1, sticky="ew", padx=(8, 0), pady=5)
            self.metric_labels[n][metric] = lbl

        # ── Separator ───────────────────────────────────────
        ttk.Separator(body, orient="vertical").grid(
            row=0, column=1, sticky="ns", padx=6)

        # ── Info fields ─────────────────────────────────────
        il = tk.Frame(body, bg=COLOR_PANEL)
        il.grid(row=0, column=2, sticky="nsew", padx=(0, 8))
        il.columnconfigure(1, weight=1)

        tk.Label(il, text="FIELD", font=(FONT, 8, "bold"),
                 bg=COLOR_PANEL, fg=COLOR_MUTED).grid(row=0, column=0, sticky="w")
        tk.Label(il, text="VALUE", font=(FONT, 8, "bold"),
                 bg=COLOR_PANEL, fg=COLOR_MUTED).grid(row=0, column=1)
        ttk.Separator(il, orient="horizontal").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        for i, (key, label) in enumerate([
                ("MCC/MNC", "MCC/MNC"), ("CellID", "CellID"),
                ("Band", "Band"),       ("Channel", "Channel Type")]):
            tk.Label(il, text=f"{label}:",
                     font=(FONT, 10), bg=COLOR_PANEL, fg=COLOR_TEXT,
                     anchor="w").grid(row=i + 2, column=0, sticky="w", pady=5)
            lbl = tk.Label(il, text="--", width=16,
                           bg=COLOR_BORDER, fg=COLOR_TEXT,
                           font=(FONT, 9), anchor="w", padx=4)
            lbl.grid(row=i + 2, column=1, sticky="ew", padx=(6, 0), pady=5)
            self.info_labels[n][key] = lbl

        # ── Separator ───────────────────────────────────────
        ttk.Separator(body, orient="vertical").grid(
            row=0, column=3, sticky="ns", padx=6)

        # ── Quality box ─────────────────────────────────────
        qf = tk.Frame(body, bg=COLOR_PANEL)
        qf.grid(row=0, column=4, sticky="nsew")
        qf.rowconfigure(1, weight=1)
        qf.columnconfigure(0, weight=1)
        tk.Label(qf, text="Signal Quality", font=(FONT, 9, "bold"),
                 bg=COLOR_PANEL, fg=COLOR_MUTED).grid(row=0, column=0, pady=(0, 4))
        q = tk.Label(qf, text="--",
                     bg=COLOR_BORDER, fg=COLOR_TEXT,
                     font=(FONT, 16, "bold"), anchor="center", justify="center")
        q.grid(row=1, column=0, sticky="nsew", pady=(0, 4))
        self.quality_labels[n] = q

    # ─────────────────────────────────────────────────────────
    # Test lifecycle
    # ─────────────────────────────────────────────────────────

    def start_test(self):
        if self.running:
            return

        urls: Dict[int, str] = {}
        for n in range(1, self.num_sensors + 1):
            url = self.url_vars[n].get().strip()
            if not url:
                messagebox.showwarning("URL Missing",
                                       f"Please enter Sensor {n} URL.")
                return
            # Auto-add scheme — same as the web app
            if "." in url and not url.startswith("http"):
                url = "https://" + url
                self.url_vars[n].set(url)
            urls[n] = url

        # Reset per-session state
        for n in range(1, self.num_sensors + 1):
            self.stores[n].clear()
            self._connected[n]      = None
            self._first_conn[n]     = False
            self._last_rec[n]       = None
            self._disc_since[n]     = None
            self._disc_committed[n] = False
            self._stop_events[n].clear()
            self._set_count(n, 0)

        self.running      = True
        self.test_start   = datetime.now()
        self._session_saved = False
        self._destroying  = False

        # One polling thread per sensor
        for n in range(1, self.num_sensors + 1):
            t = threading.Thread(target=self._poll_thread,
                                 args=(n, urls[n]),
                                 daemon=True, name=f"Poll-S{n}")
            t.start()

        self.run_btn.config(text="⏳ Running…", state="disabled")
        self.stop_btn.config(state="normal")
        self._tick_timer()

    def stop_test(self):
        self._hard_stop()

    def reset_test(self):
        self._hard_stop()
        for n in range(1, self.num_sensors + 1):
            self.stores[n].clear()
            self._connected[n]      = None
            self._first_conn[n]     = False
            self._last_rec[n]       = None
            self._disc_since[n]     = None
            self._disc_committed[n] = False
            self._blank_sensor(n)
            self._set_count(n, 0)
            self.status_labels[n].config(text="Idle", bg=COLOR_BORDER, fg=COLOR_TEXT)
        self.test_start     = None
        self._session_saved = False
        try:
            self.timer_label.config(
                text=_fmt(MAX_DURATION_SECS), bg="#FEF9C3", fg="#92400E")
            self.elapsed_label.config(text="00:00", bg=COLOR_BORDER)
        except tk.TclError:
            pass

    def save_report(self):
        total = sum(s.count for s in self.stores.values())
        if total == 0:
            messagebox.showwarning("No Data",
                "No measurements collected yet.\nRun the test first.")
            return
        if self._session_saved:
            messagebox.showinfo("Already Saved",
                "This test's data has already been saved.\n"
                "Press Reset and run a new test to save again.")
            return

        plant    = self.plant_var.get().strip()
        building = self.building_var.get().strip()
        asset    = self.asset_var.get().strip()

        # Same Plant + same Building → append to existing file
        ctx    = self._report_context
        append = (ctx is not None
                  and ctx[0] == plant and ctx[1] == building
                  and os.path.exists(ctx[2]))

        if append:
            file_path = ctx[2]
        else:
            file_path = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Excel files", "*.xlsx")],
                initialfile=default_filename(plant, building))
            if not file_path:
                return
            self._report_context = (plant, building, file_path)

        try:
            buf = build_report_bytes(
                self.stores, self.num_sensors, plant, building, asset)
            with open(file_path, "wb") as f:
                f.write(buf.read())
        except PermissionError:
            messagebox.showerror("File Locked",
                f"Could not save — the file is open in Excel.\n"
                f"Close it and try again.\n\n{file_path}")
            return
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save report:\n{e}")
            return

        self._session_saved = True
        action = "appended to" if append else "saved as"
        messagebox.showinfo("Saved", f"Report {action}:\n{file_path}")

    def _switch_mode(self, new_num: int):
        if self.running:
            messagebox.showwarning("Active Session",
                "Please stop the current session before switching modes.")
            return
        # Preserve form values across the mode switch
        plant    = self.plant_var.get()
        building = self.building_var.get()
        asset    = self.asset_var.get()
        saved_urls = {n: self.url_vars.get(n, tk.StringVar()).get()
                      for n in range(1, max(self.num_sensors, new_num) + 1)}

        self._destroying = True
        for ev in self._stop_events.values():
            ev.set()
        for w in self.master.winfo_children():
            w.destroy()

        app = ConnectivityAgentApp(self.master, num_sensors=new_num)
        app.plant_var.set(plant)
        app.building_var.set(building)
        app.asset_var.set(asset)
        for n in range(1, new_num + 1):
            if n in saved_urls:
                app.url_vars[n].set(saved_urls[n])

    # ─────────────────────────────────────────────────────────
    # Background thread
    # ─────────────────────────────────────────────────────────

    def _poll_thread(self, sensor_num: int, url: str) -> None:
        """Poll the sensor as fast as the network allows.

        Previously we added a 1-second sleep between requests. But the
        sensor performs an upload speed test that takes ~12 seconds, during
        which it reports "disconnected". The reconnect window afterwards is
        only ~2 seconds wide. If our poll happens to fire at the wrong
        moment, we miss that window and have to wait another full 12-second
        cycle — causing timing variance of 12 seconds per missed window.

        Removing the sleep means we poll immediately after each HTTP
        response. The response time (~0.5-1 s) acts as a natural rate
        limiter, so we're not hammering the sensor. But we poll ~2× more
        often, which means we're twice as likely to catch the reconnect
        window and far less likely to need a second cycle to collect each
        sample.
        """
        stop = self._stop_events[sensor_num]
        while not stop.is_set():
            reading = poll_sensor(url)
            self.result_q.put((sensor_num, reading, datetime.now()))
            # A tiny yield so the stop event is checked promptly on Stop/Reset.
            # No sleep — the HTTP request itself (0.5-1 s) is the rate limiter.
            stop.wait(0.05)

    # ─────────────────────────────────────────────────────────
    # Main-thread: queue drainer + reading processor
    # ─────────────────────────────────────────────────────────

    def _drain_queue(self) -> None:
        """Called via master.after(100). Drains the queue on the main thread."""
        try:
            while True:
                sensor_num, reading, now = self.result_q.get_nowait()
                if self.running and not self._destroying:
                    self._process_reading(sensor_num, reading, now)
        except queue.Empty:
            pass
        if not self._destroying:
            try:
                self.master.after(100, self._drain_queue)
            except tk.TclError:
                pass

    def _process_reading(self, n: int, reading, now: datetime) -> None:
        elapsed     = (now - self.test_start).total_seconds() if self.test_start else 0
        elapsed_str = _fmt(elapsed)
        timestamp   = now.strftime("%Y-%m-%d %H:%M:%S")
        connected   = reading.connected

        if connected:
            self._first_conn[n] = True

        # ── Debounced connect/disconnect event logging ─────────────────────
        # The sensor briefly reports "disconnected" during its own upload
        # speed test (~10-12 s). We only log a Disconnected event if the
        # outage lasts >= _DISC_DEBOUNCE seconds, and only log Reconnected
        # if we previously committed a Disconnected event.
        prev = self._connected[n]

        if prev is not None and prev != connected:
            # State flipped this poll
            if not connected:
                # Went offline — start the debounce timer, DON'T log yet
                self._disc_since[n]     = now
                self._disc_committed[n] = False
            else:
                # Came back online
                if self._disc_committed[n]:
                    # We had already logged a real Disconnected → log Reconnected
                    self.stores[n].add_event("Reconnected", timestamp, elapsed_str)
                # else: disconnect was too brief and was never committed → suppress both
                self._disc_since[n]     = None
                self._disc_committed[n] = False

        elif not connected and self._disc_since[n] is not None and not self._disc_committed[n]:
            # Still offline — check if debounce window has elapsed
            gap = (now - self._disc_since[n]).total_seconds()
            if gap >= self._DISC_DEBOUNCE:
                # Outage is real — commit the Disconnected event back-dated
                # to when it actually started
                disc_ts      = self._disc_since[n].strftime("%Y-%m-%d %H:%M:%S")
                disc_elapsed = _fmt((self._disc_since[n] - self.test_start).total_seconds()
                                    if self.test_start else 0)
                self.stores[n].add_event("Disconnected", disc_ts, disc_elapsed)
                self._disc_committed[n] = True

        self._connected[n] = connected

        # ── Update status label and dashboard values ───────────────────────
        # The debounce controls BOTH the Excel event logging (above) AND
        # the visible GUI state here. During the debounce window the sensor
        # is most likely performing its own upload speed test and will be
        # back online within a second or two. Keeping the GUI showing
        # "Connected" and the last known values prevents the flickering the
        # user reported when measurements count from 1→2, 2→3 etc.
        sl = self.status_labels[n]
        if connected:
            sl.config(text="Connected", bg=COLOR_CONN, fg="white")
        elif self._disc_committed[n]:
            # Debounce has elapsed → genuine outage → show Disconnected
            # and blank the live value cells
            sl.config(text="Disconnected", bg=COLOR_DISCONN, fg="white")
            self._blank_sensor(n)
        # else: still inside the debounce window → don't touch the status
        #       label or value cells so the GUI never flickers

        if not connected:
            return   # nothing to sample regardless of debounce state

        # ── Sample-on-change: only record when values actually differ ──────
        recordable = {"rsrp": reading.rsrp, "rsrq": reading.rsrq,
                      "sinr": reading.sinr, "speed": reading.speed}
        if recordable != self._last_rec[n]:
            grade  = grade_speed(reading.speed)
            sample = Sample(
                timestamp=timestamp, elapsed=elapsed_str, connected=True,
                rssi=reading.rssi,  rsrp=reading.rsrp,
                rsrq=reading.rsrq,  sinr=reading.sinr,
                speed=reading.speed, lac=reading.lac,
                cellid=reading.cellid,
                access_technology=reading.access_technology,
                sm_2g=reading.sm_2g, mccmnc=reading.mccmnc,
                band=reading.band,   channel_type=reading.channel_type,
                grade=grade)
            self.stores[n].add_sample(sample)
            self._last_rec[n] = recordable
            self._set_count(n, self.stores[n].count)

        self._paint_sensor(n, reading)

        # ── Check stop conditions ──────────────────────────────────────────
        if all(self.stores[sn].count >= MAX_MEASUREMENTS
               for sn in range(1, self.num_sensors + 1)):
            self._trigger_stop("measurements")

    # ─────────────────────────────────────────────────────────
    # Widget helpers
    # ─────────────────────────────────────────────────────────

    def _paint_sensor(self, n: int, r) -> None:
        for metric, val in [("RSRP", r.rsrp), ("RSRQ", r.rsrq), ("Speed", r.speed)]:
            lbl = self.metric_labels[n].get(metric)
            if not lbl:
                continue
            if val is None:
                lbl.config(text="--", bg=COLOR_BORDER, fg=COLOR_TEXT)
            else:
                if metric == "RSRP":
                    grade = grade_rsrp(val)
                elif metric == "RSRQ":
                    grade = grade_rsrq(val)
                else:
                    grade = grade_speed(val)
                bg, fg = PASTEL.get(grade, PASTEL["--"])
                disp   = f"{val:.2f}" if metric == "Speed" else str(val)
                lbl.config(text=disp, bg=bg, fg=fg)

        for key, val in [("MCC/MNC", r.mccmnc), ("CellID", r.cellid),
                          ("Band", r.band),       ("Channel", r.channel_type)]:
            lbl = self.info_labels[n].get(key)
            if lbl:
                lbl.config(text=val if val else "--")

        grade, _ = self.stores[n].final_grade()
        bg  = GRADE_SOLID.get(grade, COLOR_BORDER)
        fg  = "white" if grade != "--" else COLOR_TEXT
        self.quality_labels[n].config(text=grade, bg=bg, fg=fg)

    def _blank_sensor(self, n: int) -> None:
        for lbl in self.metric_labels[n].values():
            lbl.config(text="--", bg=COLOR_BORDER, fg=COLOR_TEXT)
        for lbl in self.info_labels[n].values():
            lbl.config(text="--")
        if self.stores[n].count == 0:
            self.quality_labels[n].config(
                text="No signal", bg=COLOR_DISCONN, fg="white")

    def _set_count(self, n: int, count: int) -> None:
        lbl = self.count_labels[n]
        lbl.config(text=f"{count} / {MAX_MEASUREMENTS}")
        if count >= MAX_MEASUREMENTS:
            lbl.config(bg="#DCFCE7", fg="#166534")
        else:
            lbl.config(bg="#FEF9C3", fg="#92400E")

    # ─────────────────────────────────────────────────────────
    # Timer
    # ─────────────────────────────────────────────────────────

    def _tick_timer(self) -> None:
        if not self.running or self.test_start is None or self._destroying:
            return
        try:
            elapsed   = (datetime.now() - self.test_start).total_seconds()
            remaining = max(0, MAX_DURATION_SECS - elapsed)
            bg = "#FEE2E2" if remaining < 30 else "#FEF9C3"
            fg = "#991B1B" if remaining < 30 else "#92400E"
            self.timer_label.config(text=_fmt(remaining), bg=bg, fg=fg)
            self.elapsed_label.config(text=_fmt(elapsed))
            if elapsed >= MAX_DURATION_SECS:
                self._trigger_stop("timeout")
                return
            self.master.after(500, self._tick_timer)
        except tk.TclError:
            pass

    # ─────────────────────────────────────────────────────────
    # Stop helpers
    # ─────────────────────────────────────────────────────────

    def _hard_stop(self) -> None:
        self.running = False
        for ev in self._stop_events.values():
            ev.set()
        try:
            self.run_btn.config(text="▶  Run Check", state="normal")
            self.stop_btn.config(state="disabled")
        except tk.TclError:
            pass

    def _trigger_stop(self, reason: str) -> None:
        self._hard_stop()
        if reason == "timeout":
            messagebox.showinfo(
                "Time Limit Reached",
                f"Test duration of {_fmt(MAX_DURATION_SECS)} reached.\n"
                "Click Save Report to download the Excel file.")
        else:
            messagebox.showinfo(
                "Complete",
                f"All sensors reached {MAX_MEASUREMENTS} measurements.\n"
                "Click Save Report to download the Excel file.")
        for n in range(1, self.num_sensors + 1):
            self.status_labels[n].config(
                text="Test complete", bg=COLOR_CONN, fg="white")

    def _on_close(self) -> None:
        self._destroying = True
        self.running = False
        for ev in self._stop_events.values():
            ev.set()
        # Let poll threads finish their current wait (max 1 s) then destroy
        self.master.after(200, self.master.destroy)


# ── Entry point ───────────────────────────────────────────────────────

def launch():
    root = tk.Tk()
    ConnectivityAgentApp(root, num_sensors=1)
    root.mainloop()
