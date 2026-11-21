#!/usr/bin/env python3
"""Tkinter dashboard for Remote System Health Monitoring.

- Starts fullscreen (F11 toggles). Scrolls with wheel, scrollbar or arrow / Page keys.
- One compact card per system in a responsive grid.
- RIGHT-CLICK a system card to open just that system in a big view with large
  gauges, full details and a history chart. Click "Back" (or press Esc /
  Backspace) to return to the grid.
- Red part of a ring = unhealthy portion, white tick = threshold.
- A system whose battery hit 0 shows CHARGING, then comes back at 50%.
- A system that simply stops reporting shows UNRESPONSIVE.
"""
import csv
import json
import math
import os
import re
import time
import tkinter as tk
from collections import deque
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE_DIR, "health_reports.csv")
INFO_FILE = os.path.join(BASE_DIR, "monitor_info.json")
STALE_SECONDS = 8        # no new report for this long -> UNRESPONSIVE (match monitor --timeout)
REFRESH_MS = 1000
HISTORY = 150            # samples kept per system for the history chart

# Keep these in sync with monitor_server.py / health_agent.py
CPU_THRESHOLD = 80.0        # unhealthy when ABOVE
MEMORY_THRESHOLD = 85.0     # unhealthy when ABOVE
BATTERY_THRESHOLD = 30.0    # unhealthy when BELOW
CHARGE_TARGET = 50.0        # battery % a system returns at after charging
CHARGE_SECONDS = 30.0       # how long charging takes

NUM_COLUMNS = 10            # columns in each CSV row

FONT = "Helvetica"
BG = "#14161a"
PANEL = "#1c1f26"
CARD = "#23272f"
BORDER = "#2f3640"
TEXT = "#e8eaed"
MUTED = "#8b93a1"
TRACK = "#323843"
BLUE = "#4c9ffe"
PURPLE = "#a78bfa"
GREEN = "#34d399"
RED = "#f87171"
ORANGE = "#fbbf24"
CYAN = "#38bdf8"
GRAY = "#555c69"

CARD_W = 360
GAP = 8
GAUGE = 104
DETAIL_GAUGE = 210

STATE_STYLE = {    # state -> (pill text, pill colour, border colour)
    "healthy":   ("HEALTHY", GREEN, BORDER),
    "unhealthy": ("UNHEALTHY", RED, RED),
    "charging":  ("CHARGING", CYAN, CYAN),
    "stale":     ("UNRESPONSIVE", ORANGE, ORANGE),
}


def natural_key(text):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def format_system_name(host):
    """Converts any host identifier strictly to 'system X' format."""
    cleaned = host.lower().replace("server", "").replace("host", "").strip()
    digits = "".join(filter(str.isdigit, cleaned))
    if digits:
        return f"system {digits}"
    return f"system {cleaned}" if cleaned else "system"


def classify(m, age):
    if age > STALE_SECONDS:
        return "stale"
    if m["status"].startswith("CHARGING"):
        return "charging"
    if (m["cpu"] > CPU_THRESHOLD or m["memory"] > MEMORY_THRESHOLD
            or m["battery"] < BATTERY_THRESHOLD):
        return "unhealthy"
    return "healthy"


def describe(m, state, age):
    """Values and texts shared by the grid card and the big detail view."""
    cpu, mem, bat = m["cpu"], m["memory"], m["battery"]
    if state == "charging":
        cpu = mem = 0.0
        remaining = max(0.0, (CHARGE_TARGET - bat) / CHARGE_TARGET * CHARGE_SECONDS)
        issue = (f"Charging - battery {bat:.0f}%, back online at {CHARGE_TARGET:.0f}% "
                 f"in about {remaining:.0f}s", CYAN)
        proc = "nothing (charging)"
    elif state == "stale":
        issue = (f"No report for {age:.0f}s - showing last known values", ORANGE)
        proc = m["processes"].replace(":running", " (running)") + "  [last known]"
    else:
        issues = []
        if cpu > CPU_THRESHOLD:
            issues.append(f"CPU {cpu:.1f}% (limit {CPU_THRESHOLD:.0f}%)")
        if mem > MEMORY_THRESHOLD:
            issues.append(f"MEMORY {mem:.1f}% (limit {MEMORY_THRESHOLD:.0f}%)")
        if bat < BATTERY_THRESHOLD:
            issues.append(f"BATTERY {bat:.1f}% (min {BATTERY_THRESHOLD:.0f}%)")
        if issues:
            issue = ("Unhealthy: " + "  |  ".join(issues), RED)
        else:
            issue = ("All metrics within healthy limits", GREEN)
        proc = m["processes"].replace(":running", " (running)")
    return {"cpu": cpu, "mem": mem, "bat": bat, "issue": issue[0],
            "issue_color": issue[1], "proc": proc}


def draw_gauge(canvas, val, label, threshold, mode, dim, color, size=GAUGE, charging=False):
    """Ring gauge. mode='high': unhealthy above threshold; mode='low': unhealthy below."""
    k = size / GAUGE
    m = 11 * k
    ring = max(4, int(round(7 * k)))
    cx = cy = size / 2
    r = size / 2 - m
    val = max(0.0, min(100.0, val))
    canvas.delete("all")
    canvas.create_oval(m, m, size - m, size - m, outline=TRACK, width=ring)

    def arc(a, b, col):
        span = b - a
        if span <= 0:
            return
        canvas.create_arc(m, m, size - m, size - m, start=90 - 3.6 * a,
                          extent=-min(3.6 * span, 359.99),    # Tk draws nothing for exactly 360
                          style=tk.ARC, outline=col, width=ring)

    if charging:
        arc(0, val, CYAN)
    elif dim:
        arc(0, val, GRAY)
    elif mode == "high":
        if val <= threshold:
            arc(0, val, color)
        else:
            arc(0, threshold, color)      # healthy portion
            arc(threshold, val, RED)      # unhealthy portion above the limit
    else:
        arc(0, val, RED if val < threshold else color)

    theta = math.radians(90 - 3.6 * threshold)
    tick = 8 * k
    canvas.create_line(cx + (r - tick) * math.cos(theta), cy - (r - tick) * math.sin(theta),
                       cx + (r + tick) * math.cos(theta), cy - (r + tick) * math.sin(theta),
                       fill=TEXT, width=2)

    bad = (not dim) and (not charging) and ((mode == "high" and val > threshold) or
                                            (mode == "low" and val < threshold))
    canvas.create_text(cx, cy - 6 * k, text=f"{val:.1f}%",
                       font=(FONT, int(round(12 * k)), "bold"),
                       fill=CYAN if charging else (RED if bad else (MUTED if dim else TEXT)))
    canvas.create_text(cx, cy + 13 * k, text=("! " + label) if bad else label,
                       font=(FONT, int(round(8 * k)), "bold" if bad else "normal"),
                       fill=RED if bad else MUTED)


def set_pill(label, state):
    text, color, _ = STATE_STYLE[state]
    label.config(text=text, bg=color)


def update_gauges(canvases, d, state, size):
    dim_cm = state in ("stale", "charging")
    draw_gauge(canvases[0], d["cpu"], "CPU", CPU_THRESHOLD, "high", dim_cm, BLUE, size)
    draw_gauge(canvases[1], d["mem"], "Memory", MEMORY_THRESHOLD, "high", dim_cm, PURPLE, size)
    draw_gauge(canvases[2], d["bat"], "Battery", BATTERY_THRESHOLD, "low",
               state == "stale", GREEN, size, charging=(state == "charging"))


class HostCard:
    """One system's card in the grid. Created once, then updated in place."""

    def __init__(self, parent, host, on_open):
        self._gauge_key = None
        self.frame = tk.Frame(parent, bg=CARD, highlightthickness=2,
                            highlightbackground=BORDER, highlightcolor=BORDER)

        header = tk.Frame(self.frame, bg=CARD)
        header.pack(fill=tk.X, padx=12, pady=(10, 4))
        
        display_name = format_system_name(host)
        tk.Label(header, text=display_name, font=(FONT, 13, "bold"), fg=TEXT, bg=CARD).pack(side=tk.LEFT)
        self.pill = tk.Label(header, text="", font=(FONT, 8, "bold"), fg="#111111",
                             padx=8, pady=2)
        self.pill.pack(side=tk.RIGHT)

        gauges = tk.Frame(self.frame, bg=CARD)
        gauges.pack(fill=tk.X, padx=6, pady=2)
        self.canvases = []
        for c in range(3):
            gauges.grid_columnconfigure(c, weight=1)
            cv = tk.Canvas(gauges, width=GAUGE, height=GAUGE, bg=CARD, highlightthickness=0)
            cv.grid(row=0, column=c)
            self.canvases.append(cv)

        self.issue_lbl = tk.Label(self.frame, text="", font=(FONT, 9, "bold"), bg=CARD,
                                  anchor="w", justify=tk.LEFT, wraplength=CARD_W - 30)
        self.issue_lbl.pack(fill=tk.X, padx=12, pady=(4, 0))
        self.proc_lbl = tk.Label(self.frame, text="", font=(FONT, 10), fg=TEXT, bg=CARD,
                                 anchor="w")
        self.proc_lbl.pack(fill=tk.X, padx=12, pady=(2, 0))
        self.meta_lbl = tk.Label(self.frame, text="", font=(FONT, 8), fg=MUTED, bg=CARD,
                                 anchor="w")
        self.meta_lbl.pack(fill=tk.X, padx=12, pady=(1, 10))

        # Right-click anywhere on the card opens the big view (Button-2 = right-click on macOS)
        self._bind_tree(self.frame, lambda e, h=host: on_open(h))

    def _bind_tree(self, widget, handler):
        widget.bind("<Button-3>", handler)
        widget.bind("<Button-2>", handler)
        for child in widget.winfo_children():
            self._bind_tree(child, handler)

    def update(self, m, state, age):
        d = describe(m, state, age)
        set_pill(self.pill, state)
        border = STATE_STYLE[state][2]
        self.frame.config(highlightbackground=border, highlightcolor=border)
        self.issue_lbl.config(text=d["issue"], fg=d["issue_color"])
        self.proc_lbl.config(text=f"Now running: {d['proc']}")

        key = (d["cpu"], d["mem"], d["bat"], state)
        if key != self._gauge_key:            # skip the costly redraw when nothing changed
            self._gauge_key = key
            update_gauges(self.canvases, d, state, GAUGE)

        seen = m["received_at"].split(" ")[-1]
        self.meta_lbl.config(
            text=f"Packets out/in {m['sent']} / {m['recv']}    |    "
                 f"latency {m['latency']:.1f} ms    |    last seen {seen}")


class DetailView:
    """Big single-system view with large gauges, details and a history chart."""

    FIELDS = ["Now running", "Status", "Packets sent", "Packets received",
              "Report latency", "Last seen"]

    def __init__(self, parent, on_back):
        self.frame = tk.Frame(parent, bg=BG)
        self._hist = []

        top = tk.Frame(self.frame, bg=BG)
        top.pack(fill=tk.X, padx=20, pady=(14, 6))
        tk.Button(top, text="←  Back to all systems", command=on_back,
                  font=(FONT, 11, "bold"), fg=TEXT, bg="#2f6feb",
                  activebackground="#1f5bd6", activeforeground=TEXT,
                  relief=tk.FLAT, bd=0, padx=16, pady=8, cursor="hand2").pack(side=tk.LEFT)
        self.title = tk.Label(top, text="", font=(FONT, 26, "bold"), fg=TEXT, bg=BG)
        self.title.pack(side=tk.LEFT, padx=24)
        self.pill = tk.Label(top, text="", font=(FONT, 11, "bold"), fg="#111111",
                             padx=14, pady=5)
        self.pill.pack(side=tk.LEFT)
        tk.Label(top, text="Esc / Backspace also go back", font=(FONT, 9), fg=MUTED,
                 bg=BG).pack(side=tk.RIGHT)

        self.panel = tk.Frame(self.frame, bg=CARD, highlightthickness=2,
                            highlightbackground=BORDER, highlightcolor=BORDER)
        self.panel.pack(fill=tk.X, padx=20, pady=8)
        row = tk.Frame(self.panel, bg=CARD)
        row.pack(pady=(14, 2))
        self.canvases = []
        for c in range(3):
            cv = tk.Canvas(row, width=DETAIL_GAUGE, height=DETAIL_GAUGE, bg=CARD,
                           highlightthickness=0)
            cv.grid(row=0, column=c, padx=34)
            self.canvases.append(cv)
        self.issue = tk.Label(self.panel, text="", font=(FONT, 13, "bold"), bg=CARD)
        self.issue.pack(pady=(0, 8))

        info = tk.Frame(self.panel, bg=CARD)
        info.pack(pady=(0, 14))
        self.values = {}
        for i, name in enumerate(self.FIELDS):
            cell = tk.Frame(info, bg=CARD)
            cell.grid(row=i // 3, column=i % 3, padx=34, pady=4, sticky="w")
            tk.Label(cell, text=name.upper(), font=(FONT, 8, "bold"), fg=MUTED,
                     bg=CARD).pack(anchor="w")
            v = tk.Label(cell, text="", font=(FONT, 13, "bold"), fg=TEXT, bg=CARD,
                         anchor="w", justify=tk.LEFT, wraplength=420)
            v.pack(anchor="w")
            self.values[name] = v

        self.chart = tk.Canvas(self.frame, bg=CARD, highlightthickness=2,
                               highlightbackground=BORDER, height=200)
        self.chart.pack(fill=tk.BOTH, expand=True, padx=20, pady=(4, 16))
        self.chart.bind("<Configure>", lambda e: self.draw_history())

    def update(self, host, m, state, age, hist):
        display_name = format_system_name(host)
        self.title.config(text=display_name)
        set_pill(self.pill, state)
        border = STATE_STYLE[state][2]
        self.panel.config(highlightbackground=border, highlightcolor=border)
        update_gauges(self.canvases, describe(m, state, age), state, DETAIL_GAUGE)
        d = describe(m, state, age)
        self.issue.config(text=d["issue"], fg=d["issue_color"])
        self.values["Now running"].config(text=d["proc"])
        self.values["Status"].config(text=m["status"])
        self.values["Packets sent"].config(text=str(m["sent"]))
        self.values["Packets received"].config(text=str(m["recv"]))
        self.values["Report latency"].config(text=f"{m['latency']:.1f} ms")
        self.values["Last seen"].config(text=m["received_at"])
        self._hist = list(hist)
        self.draw_history()

    def draw_history(self):
        c = self.chart
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 120 or h < 90:
            return
        pl, pr, pt, pb = 44, 16, 30, 22
        pw, ph = w - pl - pr, h - pt - pb
        c.create_text(pl, 12, anchor="w", text=f"History (last {HISTORY} reports)",
                      font=(FONT, 10, "bold"), fill=TEXT)
        lx = w - pr
        for name, col in (("Battery", GREEN), ("Memory", PURPLE), ("CPU", BLUE)):
            c.create_text(lx, 12, anchor="e", text=name, font=(FONT, 9, "bold"), fill=col)
            lx -= 70
        for pct in (0, 25, 50, 75, 100):
            y = pt + ph * (1 - pct / 100)
            c.create_line(pl, y, w - pr, y, fill=TRACK)
            c.create_text(pl - 8, y, anchor="e", text=f"{pct}%", font=(FONT, 8), fill=MUTED)
        for thr, col in ((CPU_THRESHOLD, BLUE), (MEMORY_THRESHOLD, PURPLE),
                         (BATTERY_THRESHOLD, GREEN)):
            y = pt + ph * (1 - thr / 100)
            c.create_line(pl, y, w - pr, y, fill=col, dash=(3, 6))
        n = len(self._hist)
        if n < 2:
            c.create_text(w / 2, h / 2, text="Collecting history...", font=(FONT, 11),
                          fill=MUTED)
            return
        for idx, col in ((0, BLUE), (1, PURPLE), (2, GREEN)):
            pts = []
            for i, row in enumerate(self._hist):
                x = pl + pw * (i + (HISTORY - n)) / (HISTORY - 1)    # newest at the right edge
                y = pt + ph * (1 - max(0.0, min(100.0, row[idx])) / 100)
                pts += [x, y]
            c.create_line(*pts, fill=col, width=2)


class HealthDashboardUI:
    def __init__(self, root):
        self.root = root
        self.root.title("System Health Monitor Dashboard")
        self.root.geometry("1280x800")
        self.root.configure(bg=BG)
        self.root.minsize(520, 400)

        # CSV state (read incrementally)
        self.offset = 0
        self.inode = None
        self.latest = {}
        self.history = {}
        self.states = {}
        self.cards = {}
        self.sorted_hosts = []
        self.layout_key = None
        self.prev_cols = 0
        self.info = None
        self.info_mtime = None
        self.detail_host = None

        self._build_header()
        self._build_scroll_area()
        self.detail = DetailView(self.root, self.close_detail)

        self.fullscreen = False
        self.set_fullscreen(True)
        self.root.bind("<F11>", lambda e: self.set_fullscreen(not self.fullscreen))
        self.root.bind("<Escape>", self.on_escape)
        self.root.bind("<BackSpace>", lambda e: self.close_detail())
        self.root.bind("<Alt-Left>", lambda e: self.close_detail())

        self.update_data()

    # ---------- UI construction ----------
    def _build_header(self):
        top = tk.Frame(self.root, bg=PANEL)
        top.pack(fill=tk.X)

        left = tk.Frame(top, bg=PANEL)
        left.pack(side=tk.LEFT, padx=20, pady=12)
        tk.Label(left, text="Remote System Health Dashboard", font=(FONT, 18, "bold"),
                 fg=TEXT, bg=PANEL).pack(anchor="w")
        self.monitor_lbl = tk.Label(left, text="Monitor node: not detected yet",
                                    font=(FONT, 10), fg=MUTED, bg=PANEL)
        self.monitor_lbl.pack(anchor="w", pady=(2, 0))

        right = tk.Frame(top, bg=PANEL)
        right.pack(side=tk.RIGHT, padx=14, pady=10)
        self.chip_servers = self._chip(right, "REPORTING", TEXT)
        self.chip_healthy = self._chip(right, "HEALTHY", GREEN)
        self.chip_unhealthy = self._chip(right, "UNHEALTHY", RED)
        self.chip_charging = self._chip(right, "CHARGING", CYAN)
        self.chip_down = self._chip(right, "UNRESPONSIVE", ORANGE)

        self.bar = tk.Frame(self.root, bg=BG)
        self.bar.pack(fill=tk.X, padx=20, pady=(10, 0))
        self.filter_var = tk.StringVar(value="all")
        
        for text, value in (("All", "all"), ("Healthy", "healthy"), ("Unhealthy", "unhealthy"),
                            ("Charging", "charging")):
            tk.Radiobutton(self.bar, text=text, value=value, variable=self.filter_var,
                           indicatoron=False, command=self.relayout,
                           font=(FONT, 9, "bold"), fg=TEXT, bg=PANEL, selectcolor="#2f6feb",
                           activebackground="#2a3040", activeforeground=TEXT,
                           relief=tk.FLAT, bd=0, padx=14, pady=5, cursor="hand2"
                           ).pack(side=tk.LEFT, padx=(0, 6))
        self.refresh_lbl = tk.Label(self.bar, text="", font=(FONT, 9), fg=MUTED, bg=BG)
        self.refresh_lbl.pack(side=tk.RIGHT)
        tk.Label(self.bar, text="Right-click a system for the big view  |  F11 fullscreen     ",
                 font=(FONT, 9), fg=MUTED, bg=BG).pack(side=tk.RIGHT)

    def _chip(self, parent, caption, color):
        f = tk.Frame(parent, bg=BG)
        f.pack(side=tk.LEFT, padx=5)
        val = tk.Label(f, text="0", font=(FONT, 18, "bold"), fg=color, bg=BG, padx=16)
        val.pack(pady=(6, 0))
        tk.Label(f, text=caption, font=(FONT, 8, "bold"), fg=MUTED, bg=BG, padx=10
                 ).pack(pady=(0, 6))
        return val

    def _build_scroll_area(self):
        self.wrap = tk.Frame(self.root, bg=BG)
        self.wrap.pack(fill=tk.BOTH, expand=True, padx=14, pady=(8, 14))
        self.canvas = tk.Canvas(self.wrap, bg=BG, highlightthickness=0, yscrollincrement=24)
        self.scrollbar = tk.Scrollbar(self.wrap, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.grid_frame = tk.Frame(self.canvas, bg=BG)
        self.win = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>",
                            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        self.placeholder = tk.Label(self.grid_frame, text="", font=(FONT, 12), fg=MUTED,
                                    bg=BG, justify=tk.CENTER, pady=60)

        def scroll(units=None, pages=None, to=None):
            if self.detail_host:
                return
            if to is not None:
                self.canvas.yview_moveto(to)
            elif pages is not None:
                self.canvas.yview_scroll(pages, "pages")
            else:
                self.canvas.yview_scroll(units, "units")

        self.root.bind_all("<MouseWheel>", lambda e: scroll(units=-3 if e.delta > 0 else 3))
        self.root.bind_all("<Button-4>", lambda e: scroll(units=-3))
        self.root.bind_all("<Button-5>", lambda e: scroll(units=3))
        self.root.bind("<Up>", lambda e: scroll(units=-2))
        self.root.bind("<Down>", lambda e: scroll(units=2))
        self.root.bind("<Prior>", lambda e: scroll(pages=-1))
        self.root.bind("<Next>", lambda e: scroll(pages=1))
        self.root.bind("<Home>", lambda e: scroll(to=0))
        self.root.bind("<End>", lambda e: scroll(to=1))

    def set_fullscreen(self, on):
        self.fullscreen = on
        try:
            self.root.attributes("-fullscreen", on)
        except tk.TclError:
            try:
                self.root.state("zoomed" if on else "normal")
            except tk.TclError:
                pass

    def on_escape(self, event=None):
        if self.detail_host:
            self.close_detail()
        else:
            self.set_fullscreen(False)

    def _on_canvas_resize(self, event):
        self.canvas.itemconfig(self.win, width=event.width)
        self.relayout()

    def open_detail(self, host):
        if host not in self.latest:
            return
        self.detail_host = host
        self.bar.pack_forget()
        self.wrap.pack_forget()
        self.detail.frame.pack(fill=tk.BOTH, expand=True)
        self.root.update_idletasks()
        self.refresh_detail()

    def close_detail(self):
        if not self.detail_host:
            return
        self.detail_host = None
        self.detail.frame.pack_forget()
        self.bar.pack(fill=tk.X, padx=20, pady=(10, 0))
        self.wrap.pack(fill=tk.BOTH, expand=True, padx=14, pady=(8, 14))
        self.layout_key = None
        self.relayout()

    def refresh_detail(self):
        host = self.detail_host
        if not host:
            return
        m = self.latest.get(host)
        if m is None:
            self.close_detail()
            return
        age = self.age_seconds(m["received_at"])
        self.detail.update(host, m, self.states.get(host, "healthy"), age,
                           self.history.get(host, ()))

    def reset_state(self):
        self.offset = 0
        self.latest.clear()
        self.history.clear()
        self.states.clear()
        for card in self.cards.values():
            card.frame.destroy()
        self.cards.clear()
        self.sorted_hosts = []
        self.layout_key = None

    def poll_csv(self):
        try:
            st = os.stat(CSV_FILE)
        except OSError:
            if self.latest or self.offset:
                self.reset_state()
            self.inode = None
            return
        if st.st_ino != self.inode or st.st_size < self.offset:
            self.reset_state()
            self.inode = st.st_ino
        try:
            with open(CSV_FILE, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except OSError:
            return
        end = chunk.rfind(b"\n")
        if end == -1:
            return
        self.offset += end + 1
        text = chunk[:end + 1].decode("utf-8", errors="replace")
        for row in csv.reader(text.splitlines()):
            if len(row) < NUM_COLUMNS or row[0] == "received_at":
                continue
            try:
                m = {
                    "received_at": row[0],
                    "cpu": float(row[2]),
                    "memory": float(row[3]),
                    "battery": float(row[4]) if row[4] != "" else 100.0,
                    "processes": row[5],
                    "sent": row[6],
                    "recv": row[7],
                    "latency": float(row[8]) if row[8] != "" else 0.0,
                    "status": row[9],
                }
            except ValueError:
                continue
            host = row[1]
            self.latest[host] = m
            self.history.setdefault(host, deque(maxlen=HISTORY)).append(
                (m["cpu"], m["memory"], m["battery"]))

    def load_info(self):
        try:
            mtime = os.path.getmtime(INFO_FILE)
        except OSError:
            self.info, self.info_mtime = None, None
            return
        if mtime != self.info_mtime:
            try:
                with open(INFO_FILE) as f:
                    self.info = json.load(f)
                self.info_mtime = mtime
            except (OSError, ValueError):
                self.info = None

    @staticmethod
    def age_seconds(received_at):
        try:
            ts = datetime.strptime(received_at, "%Y-%m-%d %H:%M:%S")
            return (datetime.now() - ts).total_seconds()
        except ValueError:
            return 0.0

    def passes_filter(self, host):
        want = self.filter_var.get()
        state = self.states.get(host)
        if want == "all":
            return True
        return state == want

    def relayout(self):
        if self.detail_host:
            return
        width = self.canvas.winfo_width()
        cols = max(1, width // (CARD_W + 2 * GAP))
        visible = tuple(h for h in self.sorted_hosts if self.passes_filter(h))
        key = (visible, cols)
        if key == self.layout_key:
            return
        self.layout_key = key

        for card in self.cards.values():
            card.frame.grid_forget()
        self.placeholder.grid_forget()
        for c in range(max(self.prev_cols, cols)):
            self.grid_frame.grid_columnconfigure(c, weight=1 if c < cols else 0,
                                                 uniform="card" if c < cols else "")
        self.prev_cols = cols

        if not visible:
            if not self.latest:
                msg = ("Waiting for health data...\n"
                       "Make sure topology.py is running (and its CSV file exists).")
            else:
                msg = "No systems match this filter."
            self.placeholder.config(text=msg)
            self.placeholder.grid(row=0, column=0, columnspan=cols, sticky="ew")
            return
        for i, host in enumerate(visible):
            self.cards[host].frame.grid(row=i // cols, column=i % cols, sticky="nsew",
                                        padx=GAP, pady=GAP)

    def update_data(self):
        self.poll_csv()
        self.load_info()

        counts = {"healthy": 0, "unhealthy": 0, "charging": 0, "stale": 0}
        for host, m in self.latest.items():
            if host not in self.cards:
                self.cards[host] = HostCard(self.grid_frame, host, self.open_detail)
            age = self.age_seconds(m["received_at"])
            state = classify(m, age)
            self.states[host] = state
            self.cards[host].update(m, state, age)
            counts[state] += 1
        self.sorted_hosts = sorted(self.latest, key=natural_key)

        expected = self.info["servers"] if self.info else None
        self.chip_servers.config(
            text=f"{len(self.latest)}/{expected}" if expected else str(len(self.latest)))
        self.chip_healthy.config(text=str(counts["healthy"]))
        self.chip_unhealthy.config(text=str(counts["unhealthy"]))
        self.chip_charging.config(text=str(counts["charging"]))
        self.chip_down.config(text=str(counts["stale"]))

        if self.info:
            self.monitor_lbl.config(
                text=f"Monitor node: {self.info.get('monitor_name', 'mon')} "
                     f"({self.info.get('monitor_ip', '?')})  -  collecting reports "
                     f"from {self.info['servers']} systems")
        self.refresh_lbl.config(text=f"Last refreshed {time.strftime('%H:%M:%S')}")

        self.relayout()
        self.refresh_detail()
        self.root.after(REFRESH_MS, self.update_data)


if __name__ == "__main__":
    root = tk.Tk()
    app = HealthDashboardUI(root)
    root.mainloop()
