"""PartForge desktop app: describe a part, let a local AI design it in FreeCAD, then drive it from the drawing.

The engineering drawing is the center of the app. Double-click a blue driving dimension, type a value
(60, 2.5 in) or an equation (=width*2): the change flows to the parameter table, the Part Map, a hidden
FreeCAD rebuild and the AI's context. Right-click the drawing to redline a change for the AI.
"""
import copy
import math
import os
import queue
import shutil
import threading
import time
import traceback
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

import apps
import core

C = {"bg": "#e9ece6", "panel": "#f6f7f3", "sheet": "#fcfcf9", "ink": "#1b2530", "hidden": "#9aa4ae",
     "dim": "#0b5cb5", "hot": "#e8590c", "warn": "#c77700", "red": "#cf2e2e", "muted": "#5f6a74",
     "line": "#c9cfc6", "accent": "#0b5cb5", "ok": "#2b8a3e", "req": "#e8eef7", "fact": "#eaf4ea",
     "param": "#fdf3e1", "feat": "#efe9f7", "note": "#f1f1ee"}
FONT, SMALL, BOLD = ("Segoe UI", 10), ("Segoe UI", 9), ("Segoe UI", 10, "bold")
MONO, DIMF = ("Consolas", 10), ("Segoe UI", 9, "bold")
VIEW_LABEL = {"top": "TOP", "front": "FRONT", "right": "RIGHT", "iso": "ISOMETRIC"}
VIEW_AXES = {"top": ("X", "Y"), "front": ("X", "Z"), "right": ("Y", "Z")}
NO_AI_HELP = ("No local AI is answering at {url}.\n"
              "1. Install Ollama from https://ollama.com\n"
              "2. In a terminal run:  ollama pull qwen2.5-coder:7b\n"
              "PartForge starts Ollama hidden in the background after that. LM Studio or any "
              "OpenAI-compatible server works too (Settings).")


# ============================================================================ dialogs
class Dialog(tk.Toplevel):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.configure(bg=C["panel"])
        self.result = None
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())

    def run(self):
        self.update_idletasks()
        p = self.master
        x = p.winfo_rootx() + (p.winfo_width() - self.winfo_width()) // 2
        y = p.winfo_rooty() + max(20, (p.winfo_height() - self.winfo_height()) // 3)
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.wait_window()
        return self.result

    def buttons(self, specs):
        row = ttk.Frame(self.body)
        row.grid(row=99, column=0, columnspan=3, sticky="e", pady=(14, 0))
        for text, cmd, style in specs:
            ttk.Button(row, text=text, command=cmd, style=style).pack(side="left", padx=(8, 0))


def _tab_moves_focus(w):
    w.bind("<Tab>", lambda e: (e.widget.tk_focusNext().focus_set(), "break")[1])


class IntakeDialog(Dialog):
    """The part brief. Its answers drive the web research and the AI's first design."""
    BLANK = "Blank: the AI designs it from scratch"

    def __init__(self, parent, brief=None, editing=False):
        super().__init__(parent, "Edit part brief" if editing else "New part")
        ttk.Label(self.body, text="Edit the brief" if editing else "What are you making?", style="H1.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(self.body, style="Muted.TLabel", wraplength=620, justify="left",
                  text="Model numbers and measured sizes make the web research and the first design much better. "
                       "Only the name is required.").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 12))
        self.widgets = {}
        for r, (key, label, lines, default) in enumerate(core.BRIEF_FIELDS, start=2):
            ttk.Label(self.body, text=label, wraplength=250, justify="left").grid(row=r, column=0, sticky="nw", pady=4, padx=(0, 12))
            val = (brief or {}).get(key, default)
            if lines == 1:
                w = ttk.Entry(self.body, width=64, font=FONT)
                w.insert(0, val)
            else:
                w = tk.Text(self.body, height=lines, width=64, wrap="word", font=FONT, relief="solid", bd=1,
                            highlightthickness=0, padx=4, pady=3)
                w.insert("1.0", val)
                _tab_moves_focus(w)
            w.grid(row=r, column=1, sticky="ew", pady=4)
            self.widgets[key] = w
        self.template = tk.StringVar(value=self.BLANK)
        if not editing:
            self.templates = {n: c for n, _, c in core.list_templates()}
            ttk.Label(self.body, text="Start from").grid(row=50, column=0, sticky="nw", pady=(10, 4))
            ttk.Combobox(self.body, textvariable=self.template, state="readonly", width=62,
                         values=[self.BLANK] + [f"{n}: {d}" for n, d, _ in core.list_templates()]).grid(
                row=50, column=1, sticky="ew", pady=(10, 4))
            ttk.Label(self.body, style="Muted.TLabel", text="A verified template gives small local models a working part "
                      "to adapt instead of starting from nothing.").grid(row=51, column=1, sticky="w")
        self.widgets["name"].focus_set()
        if editing:
            self.buttons([("Cancel", self.destroy, "TButton"), ("Save", lambda: self.finish(False), "TButton"),
                          ("Save and research again", lambda: self.finish(True), "Accent.TButton")])
        else:
            self.buttons([("Cancel", self.destroy, "TButton"), ("Create without research", lambda: self.finish(False), "TButton"),
                          ("Create and research", lambda: self.finish(True), "Accent.TButton")])

    def finish(self, research):
        brief = {k: (w.get() if isinstance(w, ttk.Entry) else w.get("1.0", "end")).strip() for k, w in self.widgets.items()}
        if not brief["name"]:
            messagebox.showwarning("Part name", "Give the part a name.", parent=self)
            return
        name = self.template.get().split(":")[0]
        self.result = (brief, research, getattr(self, "templates", {}).get(name))
        self.destroy()


class SettingsDialog(Dialog):
    def __init__(self, parent, settings):
        super().__init__(parent, "Settings")
        self.s = settings
        b = self.body
        ttk.Label(b, text="AI model", style="H2.TLabel").grid(row=0, column=0, sticky="w", columnspan=3)
        self.url = tk.StringVar(value=settings["base_url"])
        self.model = tk.StringVar(value=settings["model"])
        self.key = tk.StringVar(value=settings.api_key)
        self.ctx = tk.IntVar(value=settings["context_tokens"])
        ttk.Label(b, text="Server").grid(row=1, column=0, sticky="w", pady=3)
        url = ttk.Combobox(b, textvariable=self.url, values=list(core.PRESETS.values()), width=48)
        url.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(b, text="Ollama  :11434   ·   LM Studio  :1234", style="Muted.TLabel").grid(row=1, column=2, sticky="w", padx=8)
        ttk.Label(b, text="Model").grid(row=2, column=0, sticky="w", pady=3)
        self.models = ttk.Combobox(b, textvariable=self.model, width=48)
        self.models.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Button(b, text="Test connection", command=self.test).grid(row=2, column=2, sticky="w", padx=8)
        ttk.Label(b, text="API key (optional)").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(b, textvariable=self.key, show="•", width=50).grid(row=3, column=1, sticky="ew", pady=3)
        ttk.Label(b, text="encrypted for this Windows user", style="Muted.TLabel").grid(row=3, column=2, sticky="w", padx=8)
        ttk.Label(b, text="Context size (tokens)").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Spinbox(b, from_=2048, to=262144, increment=2048, textvariable=self.ctx, width=10).grid(row=4, column=1, sticky="w", pady=3)
        self.status = ttk.Label(b, text="", style="Muted.TLabel", wraplength=520)
        self.status.grid(row=5, column=0, columnspan=3, sticky="w", pady=(2, 8))

        ttk.Label(b, text="Behaviour", style="H2.TLabel").grid(row=6, column=0, sticky="w", columnspan=3, pady=(8, 2))
        self.flags = {}
        for r, (k, text) in enumerate([("auto_fix", "Let the AI repair the model when a change breaks the build"),
                                       ("web_search", "Research the web when starting a part"),
                                       ("lean_coding", "Token Thrift: lean-coding prompt module"),
                                       ("fast_reasoning", "Token Thrift: fast-reasoning prompt module")], start=7):
            self.flags[k] = tk.BooleanVar(value=settings[k])
            ttk.Checkbutton(b, text=text, variable=self.flags[k]).grid(row=r, column=0, columnspan=3, sticky="w")

        ttk.Label(b, text="Printer build volume (mm)").grid(row=11, column=0, sticky="w", pady=(8, 3))
        bedrow = ttk.Frame(b)
        bedrow.grid(row=11, column=1, sticky="w", pady=(8, 3))
        self.bed = [tk.StringVar(value=core.fmt(v)) for v in settings["bed"]]
        for i, v in enumerate(self.bed):
            ttk.Entry(bedrow, textvariable=v, width=7).pack(side="left")
            if i < 2:
                ttk.Label(bedrow, text=" × ").pack(side="left")
        ttk.Label(b, text="Files", style="H2.TLabel").grid(row=12, column=0, sticky="w", columnspan=3, pady=(10, 2))
        self.fc = tk.StringVar(value=settings["freecad_cmd"] or core.find_freecad())
        self.pdir = tk.StringVar(value=settings["projects_dir"])
        for r, (label, var, pick) in enumerate([("FreeCAD (freecadcmd.exe)", self.fc, self.pick_fc),
                                                ("Projects folder", self.pdir, self.pick_dir)], start=13):
            ttk.Label(b, text=label).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(b, textvariable=var, width=50).grid(row=r, column=1, sticky="ew", pady=3)
            ttk.Button(b, text="Browse…", command=pick).grid(row=r, column=2, sticky="w", padx=8)
        self.buttons([("Cancel", self.destroy, "TButton"), ("Save", self.save, "Accent.TButton")])
        self.test()

    def pick_fc(self):
        p = filedialog.askopenfilename(parent=self, title="freecadcmd.exe", filetypes=[("FreeCAD console", "freecadcmd.exe"), ("Programs", "*.exe")])
        if p:
            self.fc.set(p)

    def pick_dir(self):
        p = filedialog.askdirectory(parent=self, initialdir=self.pdir.get())
        if p:
            self.pdir.set(p)

    def _temp_settings(self):
        t = core.Settings(dict(self.s))
        t["base_url"] = self.url.get().strip()
        t.api_key = self.key.get().strip()
        return t

    def test(self):
        self.status.configure(text="Connecting…")
        t = self._temp_settings()

        def work():
            try:
                models = core.ensure_server(t)
                msg = f"Connected. {len(models)} model(s) available." if models else "Connected, but no models installed (ollama pull qwen2.5-coder:7b)."
            except core.LLMError as e:
                models, msg = [], str(e)
            self.after(0, lambda: self._tested(models, msg))
        threading.Thread(target=work, daemon=True).start()

    def _tested(self, models, msg):
        if not self.winfo_exists():
            return
        self.models.configure(values=models)
        if models and self.model.get() not in models:
            self.model.set(models[0])
        self.status.configure(text=msg)

    def save(self):
        s = self.s
        s["base_url"], s["model"] = self.url.get().strip(), self.model.get().strip()
        s.api_key = self.key.get().strip()
        s["context_tokens"] = max(2048, int(self.ctx.get() or 16384))
        for k, v in self.flags.items():
            s[k] = v.get()
        s["freecad_cmd"], s["projects_dir"] = self.fc.get().strip(), self.pdir.get().strip()
        try:
            s["bed"] = [float(v.get()) for v in self.bed]
        except ValueError:
            pass
        s.save()
        self.result = True
        self.destroy()


class AppEditDialog(Dialog):
    def __init__(self, parent, app=None):
        super().__init__(parent, "Edit app" if app else "Add app")
        a = app or {"name": "", "kind": "slicer", "sends": "stl", "exe": "", "args": "{files}"}
        self.builtin = bool(a.get("builtin"))
        self.v = {k: tk.StringVar(value=a.get(k, "")) for k in ("name", "exe", "args")}
        self.v["exe"].set(a.get("exe") or a.get("path") or "")
        self.v["args"].set(a.get("args") or "{files}")
        self.kind = tk.StringVar(value=apps.KINDS[a["kind"]])
        self.sends = tk.StringVar(value=apps.SENDS[a["sends"]])
        b = self.body
        rows = [("Name", ttk.Entry(b, textvariable=self.v["name"], width=46)),
                ("Used for", ttk.Combobox(b, textvariable=self.kind, values=list(apps.KINDS.values()), state="readonly", width=44)),
                ("Sends", ttk.Combobox(b, textvariable=self.sends, values=list(apps.SENDS.values()), state="readonly", width=44)),
                ("Program (.exe)", ttk.Entry(b, textvariable=self.v["exe"], width=46)),
                ("Arguments", ttk.Entry(b, textvariable=self.v["args"], width=46))]
        for r, (label, w) in enumerate(rows):
            ttk.Label(b, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 10))
            w.grid(row=r, column=1, sticky="ew", pady=4)
            if self.builtin and label in ("Name", "Used for", "Sends", "Arguments"):
                w.configure(state="disabled")
        ttk.Button(b, text="Browse…", command=self.browse).grid(row=3, column=2, padx=8)
        ttk.Label(b, style="Muted.TLabel", text="{files} all files · {file} first file · {folder} its folder.  "
                  f"Use {apps.SYSTEM} to open with whatever Windows uses.").grid(row=5, column=0, columnspan=3, sticky="w")
        self.buttons([("Cancel", self.destroy, "TButton"), ("Save", self.save, "Accent.TButton")])

    def browse(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("Programs", "*.exe")])
        if p:
            self.v["exe"].set(p)

    def save(self):
        if not self.v["name"].get().strip():
            return
        rev = lambda d, label: next(k for k, v in d.items() if v == label)
        self.result = {"name": self.v["name"].get().strip(), "kind": rev(apps.KINDS, self.kind.get()),
                       "sends": rev(apps.SENDS, self.sends.get()), "exe": self.v["exe"].get().strip(),
                       "args": self.v["args"].get().strip() or "{files}"}
        self.destroy()


class AppsDialog(Dialog):
    """Connected apps: pick the quick-send app, repoint an .exe, add your own."""

    def __init__(self, parent, settings):
        super().__init__(parent, "Connected apps")
        self.s = settings
        ttk.Label(self.body, text="Connected apps", style="H1.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self.body, style="Muted.TLabel", text="★ marks the quick-send app (Ctrl+P). Double-click an app to point "
                  "PartForge at a different install.").grid(row=1, column=0, sticky="w", pady=(2, 10))
        self.tree = ttk.Treeview(self.body, columns=("for", "sends", "where"), show="tree headings", height=12)
        for col, text, w in (("#0", "App", 250), ("for", "Used for", 120), ("sends", "Sends", 190), ("where", "Installed at", 330)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=w, anchor="w")
        self.tree.grid(row=2, column=0, sticky="nsew")
        self.tree.tag_configure("missing", foreground=C["muted"])
        self.tree.bind("<Double-1>", lambda e: self.edit())
        side = ttk.Frame(self.body)
        side.grid(row=2, column=1, sticky="n", padx=(12, 0))
        for text, cmd in (("★ Quick send", self.set_quick), ("Send now", self.send_now), ("Add app…", self.add),
                          ("Edit…", self.edit), ("Remove", self.remove)):
            ttk.Button(side, text=text, command=cmd, width=14).pack(pady=3)
        self.buttons([("Done", self.destroy, "Accent.TButton")])
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for a in apps.all_apps(self.s):
            star = "★ " if a["id"] == self.s.get("quick_send") else "    "
            self.tree.insert("", "end", iid=a["id"], text=star + a["name"], tags=() if a["path"] else ("missing",),
                             values=(apps.KINDS[a["kind"]], apps.SENDS[a["sends"]], a["path"] or "not found"))

    def sel(self):
        s = self.tree.selection()
        return apps.get(self.s, s[0]) if s else None

    def set_quick(self):
        a = self.sel()
        if a:
            self.s["quick_send"] = a["id"]
            self.s.save()
            self.refresh()
            self.master.refresh_send()

    def send_now(self):
        a = self.sel()
        if a:
            self.master.send_to(a)

    def add(self):
        a = AppEditDialog(self).run()
        if a:
            a["id"] = "custom-" + str(int(time.time() * 1000))
            self.s["apps_custom"].append(a)
            self.s.save()
            self.refresh()

    def edit(self):
        a = self.sel()
        if not a:
            return
        new = AppEditDialog(self, a).run()
        if not new:
            return
        if a["builtin"]:
            self.s["app_paths"][a["id"]] = new["exe"]
        else:
            self.s["apps_custom"] = [dict(new, id=a["id"]) if c["id"] == a["id"] else c for c in self.s["apps_custom"]]
        self.s.save()
        self.refresh()
        self.master.refresh_send()

    def remove(self):
        a = self.sel()
        if not a:
            return
        if a["builtin"]:
            messagebox.showinfo("Built-in app", "Built-in apps can't be removed; they simply stay unused if not installed.", parent=self)
            return
        self.s["apps_custom"] = [c for c in self.s["apps_custom"] if c["id"] != a["id"]]
        self.s.save()
        self.refresh()


# ============================================================================ drawing
class DrawingCanvas(tk.Canvas):
    """Third-angle engineering drawing with editable driving dimensions, zoom/pan and redlines."""
    PAD, STEP = 30, 24   # px: first dimension offset from the view, spacing between stacked dimensions

    def __init__(self, parent, app):
        super().__init__(parent, bg=C["sheet"], highlightthickness=0)
        self.app = app
        self.s, self.ox, self.oy, self.autofit = None, 0.0, 0.0, True
        self.offsets, self.dimtexts, self.dimcolor = {}, {}, {}
        self.editing, self.pending, self.drag, self.busy = None, False, None, False
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<MouseWheel>", self.on_wheel)
        for btn in ("1", "2"):
            self.bind(f"<ButtonPress-{btn}>", self.on_press)
            self.bind(f"<B{btn}-Motion>", self.on_drag)
        self.bind("<Button-3>", self.on_menu)
        self.bind("<Key-f>", lambda e: self.fit_view())
        self.tag_bind("dimtext", "<Double-1>", self.on_dim_double)
        self.tag_bind("dimtext", "<Enter>", self.on_dim_enter)
        self.tag_bind("dimtext", "<Leave>", lambda e: self.highlight(None))

    # --- geometry helpers
    def xy(self, x, y):
        return self.ox + x * self.s, self.oy - y * self.s

    def layout(self, views, g=None):
        F, T, R = views["front"]["bbox"], views["top"]["bbox"], views["right"]["bbox"]
        ext = max(F[2] - F[0], F[3] - F[1], T[3] - T[1], R[2] - R[0], 1)
        g = g or ext * 0.32 + 10
        off = {"front": (0, 0), "top": (0, F[3] + g - T[1]), "right": (F[2] + g - R[0], 0)}
        if "iso" in views:
            I = views["iso"]["bbox"]
            off["iso"] = (off["right"][0] + R[0] - I[0], off["top"][1] + T[1] - I[1])
        return off, g

    def fit(self, views, g):
        boxes = [(b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)
                 for v, (dx, dy) in self.offsets.items() for b in [views[v]["bbox"]]]
        x0, y0 = min(b[0] for b in boxes) - g * 0.45, min(b[1] for b in boxes) - g * 0.45
        x1, y1 = max(b[2] for b in boxes) + g * 0.45, max(b[3] for b in boxes) + g * 0.45
        W, H = self.winfo_width(), self.winfo_height() - 34 - 104   # keep clear of the hint line and title block
        self.s = max(0.05, min(W / (x1 - x0), H / (y1 - y0)))
        self.ox = W / 2 - (x0 + x1) / 2 * self.s
        self.oy = 34 + H / 2 + (y0 + y1) / 2 * self.s

    def fit_view(self):
        self.autofit = True
        self.redraw()

    def hit_view(self, sx, sy):
        views = (self.app.report or {}).get("views")
        if not views or not self.s:
            return None
        x, y = (sx - self.ox) / self.s, (self.oy - sy) / self.s
        for v in ("top", "front", "right"):
            b, (dx, dy) = views[v]["bbox"], self.offsets.get(v, (0, 0))
            m = max(b[2] - b[0], b[3] - b[1]) * 0.15 + 4
            if b[0] - m <= x - dx <= b[2] + m and b[1] - m <= y - dy <= b[3] + m:
                return v, x - dx, y - dy
        return None

    # --- drawing
    def redraw(self):
        if self.editing:
            self.pending = True
            return
        self.delete("all")
        W, H = self.winfo_width(), self.winfo_height()
        if W < 60 or not self.app.project:
            return
        rep = self.app.report
        views = (rep or {}).get("views")
        if views:
            # Views sit apart in model mm but dimensions stack in screen px: widen the gap until the stack fits.
            per_view = max([sum(d["view"] == v for d in rep.get("dims", [])) for v in views] + [0])
            need_px = self.PAD + per_view * self.STEP + 34
            self.offsets, g = self.layout(views)
            for _ in range(3):
                if self.autofit or self.s is None:
                    self.fit(views, g)
                if g * self.s >= need_px:
                    break
                self.offsets, g = self.layout(views, need_px / self.s)
            stacks = self.draw_dims(views)
            for v, data in views.items():
                self.draw_view(v, data, stacks)
            self.draw_markups(views)
        else:
            msg = "Designing the first version…" if self.app.busy_llm or self.busy else "No geometry yet. Press Build (F5)."
            self.create_text(W / 2, H / 2, text=msg, fill=C["muted"], font=("Segoe UI", 13))
        self.draw_titleblock(W, H)
        self.create_text(14, 12, anchor="nw", fill=C["muted"], font=SMALL, tags="tip",
                         text="Double-click a blue dimension to change it  ·  right-click to redline  ·  wheel zooms, drag pans, F fits")
        banners = []
        if self.busy:
            banners.append(("Rebuilding in FreeCAD…", C["accent"]))
        last = self.app.last_report
        if last and not last.get("ok"):
            banners.append(("Last build failed: showing the last good geometry. Details in the chat.", C["red"]))
        elif rep:
            probs = core.report_problems(rep, self.app.project.params, self.app.settings["bed"])
            if probs:
                banners.append((f"⚠ {probs[0]}" + (f"  (+{len(probs) - 1} more)" if len(probs) > 1 else ""), C["warn"]))
        for i, (text, color) in enumerate(banners):
            t = self.create_text(W / 2, 40 + i * 26, text=text, fill="white", font=BOLD)
            x0, y0, x1, y1 = self.bbox(t)
            r = self.create_rectangle(x0 - 10, y0 - 4, x1 + 10, y1 + 4, fill=color, outline="")
            self.tag_lower(r, t)

    def draw_view(self, v, data, stacks):
        dx, dy = self.offsets[v]
        for key, color, width, dash in (("hid", C["hidden"], 1, (4, 3)), ("vis", C["ink"], 1.6, None)):
            for line in data[key]:
                if len(line) < 2:
                    continue
                pts = []
                for x, y in line:
                    pts += self.xy(x + dx, y + dy)
                self.create_line(*pts, fill=color, width=width, dash=dash, capstyle="round")
        b = data["bbox"]
        cx, _ = self.xy((b[0] + b[2]) / 2 + dx, 0)
        _, bottom = self.xy(0, b[1] + dy)
        n = stacks.get((v, "bottom"), 0)
        size = f"{core.fmt(b[2] - b[0])} × {core.fmt(b[3] - b[1])}" if v != "iso" else ""
        self.create_text(cx, bottom + 14 + (self.PAD + n * self.STEP if n else 0), anchor="n", fill=C["muted"],
                         font=("Segoe UI", 9, "bold"), text=f"{VIEW_LABEL[v]}   ({size})" if size else VIEW_LABEL[v])

    def draw_dims(self, views):
        params = self.app.project.params
        self.dimtexts, self.dimcolor = {}, {}
        stacks = {}
        dims = sorted(self.app.report.get("dims", []), key=lambda d: d["measured"])
        for d in dims:
            v = d["view"]
            if v not in self.offsets:
                continue
            dx, dy = self.offsets[v]
            b = views[v]["bbox"]
            X0, Y0 = self.xy(b[0] + dx, b[3] + dy)
            X1, Y1 = self.xy(b[2] + dx, b[1] + dy)
            a, e = self.xy(d["a"][0] + dx, d["a"][1] + dy), self.xy(d["b"][0] + dx, d["b"][1] + dy)
            p = params.get(d["param"])
            value = p["value"] if p else d["measured"]
            color = C["hot"] if p and abs(d["measured"] - value) > 0.05 else C["dim"] if p else C["muted"]
            text = ("Ø" if d["kind"] == "dia" else "") + core.fmt(value) + ("  fx" if p and p.get("expr") else "")
            tag = f"dim:{d['param']}"
            self.dimcolor[tag] = color
            kw = {"fill": color, "tags": ("dim", tag)}
            if d["kind"] == "dia":
                lx, ly = e[0] + 26, e[1] - 26
                self.create_line(lx + 44, ly, lx, ly, e[0], e[1], arrow="last", arrowshape=(8, 9, 3), width=1, **kw)
                tx, ty, anchor, angle = lx + 3, ly - 1, "sw", 0
            elif abs(e[0] - a[0]) >= abs(e[1] - a[1]):
                side = "top" if (a[1] + e[1]) / 2 <= (Y0 + Y1) / 2 + 1 else "bottom"
                k = stacks.get((v, side), 0)
                stacks[(v, side)] = k + 1
                sgn = -1 if side == "top" else 1
                ly = (Y0 if side == "top" else Y1) + sgn * (self.PAD + k * self.STEP)
                for x, y in (a, e):
                    self.create_line(x, y + sgn * 3, x, ly + sgn * 5, width=0.8, **kw)
                self.create_line(a[0], ly, e[0], ly, arrow="both", arrowshape=(8, 9, 3), width=1, **kw)
                tx, ty, anchor, angle = (a[0] + e[0]) / 2, ly - 2, "s", 0
            else:
                side = "left" if (a[0] + e[0]) / 2 <= (X0 + X1) / 2 + 1 else "right"
                k = stacks.get((v, side), 0)
                stacks[(v, side)] = k + 1
                sgn = -1 if side == "left" else 1
                lx = (X0 if side == "left" else X1) + sgn * (self.PAD + k * self.STEP)
                for x, y in (a, e):
                    self.create_line(x + sgn * 3, y, lx + sgn * 5, y, width=0.8, **kw)
                self.create_line(lx, a[1], lx, e[1], arrow="both", arrowshape=(8, 9, 3), width=1, **kw)
                tx, ty, anchor, angle = lx - 2, (a[1] + e[1]) / 2, "s", 90
            t = self.create_text(tx, ty, text=text, font=DIMF, anchor=anchor, angle=angle,
                                 fill=color, tags=("dim", tag, "dimtext"), activefill=C["hot"])
            bg = self.create_rectangle(*self.bbox(t), fill=C["sheet"], outline="", tags=("dimbg",))
            self.tag_lower(bg, t)
            self.dimtexts[t] = d
        return stacks

    def draw_markups(self, views):
        for r in self.app.project.data["markups"]:
            if r["status"] == "resolved" or r["view"] not in self.offsets:
                continue
            dx, dy = self.offsets[r["view"]]
            x, y = self.xy(r["x"] + dx, r["y"] + dy)
            color = C["red"] if r["status"] == "open" else C["muted"]
            tags = ("markup", f"mk:{r['id']}")
            self.create_oval(x - 11, y - 11, x + 11, y + 11, outline=color, width=2, tags=tags)
            self.create_text(x, y, text=str(r["id"]), fill=color, font=BOLD, tags=tags)
            label = r["text"] if len(r["text"]) < 48 else r["text"][:46] + "…"
            self.create_text(x + 16, y, text=label + ("  ✓ sent" if r["status"] == "addressed" else ""), anchor="w",
                             fill=color, font=("Segoe UI", 9, "italic"), tags=tags)

    def draw_titleblock(self, W, H):
        proj = self.app.project
        w, h = 360, 78
        x0, y0 = W - w - 12, H - h - 12
        ink = dict(fill=C["ink"], font=SMALL, anchor="w")
        self.create_rectangle(x0, y0, x0 + w, y0 + h, outline=C["ink"], width=1.4, fill=C["sheet"])
        for yy in (26, 52):
            self.create_line(x0, y0 + yy, x0 + w, y0 + yy, fill=C["ink"])
        self.create_line(x0 + 250, y0 + 26, x0 + 250, y0 + h, fill=C["ink"])
        self.create_text(x0 + 8, y0 + 13, text=proj.name.upper(), **{**ink, "font": ("Segoe UI", 11, "bold")})
        mat = (proj.data["brief"].get("material") or "—").splitlines()[0][:36]
        self.create_text(x0 + 8, y0 + 39, text=f"MATERIAL  {mat}", **ink)
        self.create_text(x0 + 8, y0 + 65, text="UNITS mm   ·   THIRD-ANGLE PROJECTION", **ink)
        revs = proj.data.get("revisions", [])
        self.create_text(x0 + 258, y0 + 39, text=f"REV {revs[-1]['rev'] if revs else '—'}  ·  BUILD {proj.data['builds']}", **ink)
        self.create_text(x0 + 258, y0 + 65, text=time.strftime("%Y-%m-%d"), **ink)
        # third-angle symbol
        sx, sy = x0 + w - 44, y0 + 13
        self.create_oval(sx - 6, sy - 6, sx + 6, sy + 6, outline=C["ink"])
        self.create_oval(sx - 2.5, sy - 2.5, sx + 2.5, sy + 2.5, outline=C["ink"])
        self.create_polygon(sx + 12, sy - 3, sx + 26, sy - 6, sx + 26, sy + 6, sx + 12, sy + 3, outline=C["ink"], fill="")

    # --- interaction
    def highlight(self, param):
        for tag, color in self.dimcolor.items():
            self.itemconfigure(tag, fill=color)
        if param and f"dim:{param}" in self.dimcolor:
            self.itemconfigure(f"dim:{param}", fill=C["hot"])
        if not param:
            self.itemconfigure("tip", text="Double-click a blue dimension to change it  ·  right-click to redline  ·  wheel zooms, drag pans, F fits")

    def on_dim_enter(self, e):
        cur = self.find_withtag("current")
        d = self.dimtexts.get(cur[0]) if cur else None
        if not d:
            return
        self.highlight(d["param"])
        p = self.app.project.params.get(d["param"])
        tip = f"{d['param']}" + (f" = {core.fmt(p['value'])} {p['unit']}   {p['note']}" if p else "   (not a parameter)")
        if p and p.get("expr"):
            tip += f"   = {p['expr']}"
        if p and abs(d["measured"] - p["value"]) > 0.05:
            tip += f"   ⚠ geometry measures {core.fmt(d['measured'])}"
        self.itemconfigure("tip", text=tip + "   ·   double-click to edit")
        self.app.ptable.select(d["param"])

    def on_dim_double(self, e):
        cur = self.find_withtag("current")
        d = self.dimtexts.get(cur[0]) if cur else None
        if d:
            self.edit_param(d["param"], self.coords(cur[0]))
        return "break"

    def edit_param(self, name, where):
        p = self.app.project.params.get(name)
        if not p:
            messagebox.showinfo("Not a parameter", f"'{name}' isn't one of this model's parameters.", parent=self)
            return
        if self.editing:
            return
        var = tk.StringVar(value="=" + p["expr"] if p.get("expr") else core.fmt(p["value"]))
        box = tk.Frame(self, bg=C["accent"], padx=2, pady=2)
        tk.Label(box, text=f"{name}  ({p['unit']})", bg=C["accent"], fg="white", font=SMALL).pack(fill="x")
        ent = tk.Entry(box, textvariable=var, width=14, font=BOLD, justify="center", relief="flat")
        ent.pack()
        tk.Label(box, text="Enter ✓   Esc ✗   =expr links", bg=C["accent"], fg="white", font=("Segoe UI", 8)).pack(fill="x")
        win = self.create_window(where[0], where[1], window=box)
        self.editing = win

        def close(apply):
            if self.editing != win:
                return
            self.editing = None
            text = var.get()
            self.delete(win)
            box.destroy()
            if apply:
                self.app.set_param(name, text, "drawing")
            self.pending = False
            self.redraw()
        ent.bind("<Return>", lambda e: close(True))
        ent.bind("<KP_Enter>", lambda e: close(True))
        ent.bind("<Escape>", lambda e: close(False))
        ent.bind("<FocusOut>", lambda e: close(False))
        ent.select_range(0, "end")
        ent.focus_set()

    def on_press(self, e):
        self.focus_set()
        self.drag = (e.x, e.y)

    def on_drag(self, e):
        if self.drag and self.s:
            self.ox += e.x - self.drag[0]
            self.oy += e.y - self.drag[1]
            self.drag = (e.x, e.y)
            self.autofit = False
            self.redraw()

    def on_wheel(self, e):
        if not self.s:
            return
        f = 1.15 if e.delta > 0 else 1 / 1.15
        self.ox = e.x - (e.x - self.ox) * f
        self.oy = e.y - (e.y - self.oy) * f
        self.s *= f
        self.autofit = False
        self.redraw()

    def on_menu(self, e):
        if not self.app.project:
            return
        m = tk.Menu(self, tearoff=0)
        cur = self.find_withtag("current")
        tags = self.gettags(cur[0]) if cur else ()
        param = next((t[4:] for t in tags if t.startswith("dim:")), None)
        mk = next((int(t[3:]) for t in tags if t.startswith("mk:")), None)
        if param:
            m.add_command(label=f"Change {param}…", command=lambda: self.edit_param(param, (e.x, e.y)))
            m.add_command(label=f"Ask the AI about {param}", command=lambda: self.app.prefill(f"About {param}: "))
            m.add_separator()
        if mk is not None:
            m.add_command(label=f"Mark redline #{mk} resolved", command=lambda: self.app.set_redline(mk, "resolved"))
            m.add_separator()
        hit = self.hit_view(e.x, e.y)
        if hit:
            m.add_command(label="Redline here: request a change…", command=lambda: self.redline(*hit))
        m.add_command(label="Fit to window   F", command=self.fit_view)
        m.tk_popup(e.x_root, e.y_root)

    def redline(self, view, x, y):
        a, b = VIEW_AXES[view]
        text = simpledialog.askstring("Redline", f"What should change here?\n({VIEW_LABEL[view]} view, near {a}={core.fmt(round(x, 1))}, "
                                      f"{b}={core.fmt(round(y, 1))} mm)", parent=self)
        if text and text.strip():
            self.app.add_redline(view, x, y, text.strip())


class ParamTable(ttk.Frame):
    COLS = (("value", "Value", 90), ("unit", "Unit", 50), ("expr", "Equation", 150), ("range", "Range", 90), ("note", "Note", 340))

    def __init__(self, parent, app):
        super().__init__(parent, padding=(8, 6, 8, 4))
        self.app = app
        ttk.Label(self, text="PARAMETERS", style="Cap.TLabel").pack(anchor="w")
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in self.COLS], show="tree headings", height=5)
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.column("#0", width=150)
        for key, text, w in self.COLS:
            self.tree.heading(key, text=text, anchor="w")
            self.tree.column(key, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.app.drawing.highlight(self.selected()))
        self.tree.tag_configure("fx", foreground=C["accent"])

    def selected(self):
        s = self.tree.selection()
        return s[0] if s else None

    def select(self, name):
        if self.tree.exists(name):
            self.tree.selection_set(name)
            self.tree.see(name)

    def refresh(self):
        sel = self.selected()
        self.tree.delete(*self.tree.get_children())
        if not self.app.project:
            return
        for k, p in self.app.project.params.items():
            rng = f"{core.fmt(p['min'])} – {core.fmt(p['max'])}" if p.get("min") is not None and p.get("max") is not None else ""
            self.tree.insert("", "end", iid=k, text=k, tags=("fx",) if p.get("expr") else (),
                             values=(core.fmt(p["value"]), p["unit"], "=" + p["expr"] if p.get("expr") else "", rng, p.get("note", "")))
        if sel and self.tree.exists(sel):
            self.tree.selection_set(sel)

    def on_double(self, e):
        row, col = self.tree.identify_row(e.y), self.tree.identify_column(e.x)
        if not row or col not in ("#0", "#1", "#3"):
            return
        box = self.tree.bbox(row, "#1" if col == "#0" else col)
        if not box:
            return
        p = self.app.project.params[row]
        var = tk.StringVar(value="=" + p["expr"] if p.get("expr") else core.fmt(p["value"]))
        ent = ttk.Entry(self.tree, textvariable=var, font=FONT)
        ent.place(x=box[0], y=box[1], width=max(box[2], 140), height=box[3])
        done = {"v": False}

        def close(apply):
            if done["v"]:
                return
            done["v"] = True
            text = var.get()
            ent.destroy()
            if apply:
                self.app.set_param(row, text, "parameter table")
        ent.bind("<Return>", lambda ev: close(True))
        ent.bind("<Escape>", lambda ev: close(False))
        ent.bind("<FocusOut>", lambda ev: close(False))
        ent.select_range(0, "end")
        ent.focus_set()


class MapCanvas(tk.Canvas):
    """The Part Map: the AI's working memory drawn as a diagram. Edit parameters, answer questions right here."""
    COLS = [("REQUIREMENTS", "req"), ("FACTS  (from research)", "fact"), ("PARAMETERS", "param"),
            ("FEATURES", "feat"), ("DECISIONS & QUESTIONS", "note")]
    COLW, GAP = 230, 24

    def __init__(self, parent, app):
        super().__init__(parent, bg=C["sheet"], highlightthickness=0)
        self.app = app
        self.boxes = {}
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<MouseWheel>", lambda e: self.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.tag_bind("node", "<Double-1>", self.on_double)
        self.tag_bind("node", "<Enter>", self.on_enter)
        self.tag_bind("node", "<Leave>", lambda e: self.itemconfigure("edge", fill=C["line"], width=1))

    def node(self, col, y, text, kind, key, sub=""):
        x = 20 + col * (self.COLW + self.GAP)
        tags = ("node", f"n:{kind}:{key}")
        t = self.create_text(x + 10, y + 7, text=text, anchor="nw", width=self.COLW - 20, font=SMALL, fill=C["ink"], tags=tags)
        if sub:
            _, _, _, y1 = self.bbox(t)
            self.create_text(x + 10, y1 + 1, text=sub, anchor="nw", width=self.COLW - 20, font=("Segoe UI", 8),
                             fill=C["muted"], tags=tags)
        x0, y0, x1, y1 = self.bbox(*self.find_withtag(f"n:{kind}:{key}"))
        r = self.create_rectangle(x, y, x + self.COLW, y1 + 7, fill=C[kind], outline=C["line"], tags=tags)
        self.tag_lower(r)
        self.boxes[(kind, key)] = (x, y, x + self.COLW, y1 + 7)
        return y1 + 7 + 10

    def redraw(self):
        self.delete("all")
        self.boxes = {}
        proj = self.app.project
        if not proj:
            return
        self.COLW = max(140, (self.winfo_width() - 40) // 5 - self.GAP)
        m = proj.memory
        for i, (title, _) in enumerate(self.COLS):
            self.create_text(20 + i * (self.COLW + self.GAP), 14, text=title, anchor="w", font=("Segoe UI", 9, "bold"), fill=C["muted"])
        ys = [34] * 5
        for key, label, _, _ in core.BRIEF_FIELDS:
            val = proj.data["brief"].get(key, "").strip()
            if val and key != "name":
                ys[0] = self.node(0, ys[0], val if len(val) < 160 else val[:158] + "…", "req", key, label)
        for k, f in m["facts"].items():
            ys[1] = self.node(1, ys[1], f"{k} = {f['value']} {f['unit']}", "fact", k, f.get("source", ""))
        for k, p in m["params"].items():
            ys[2] = self.node(2, ys[2], f"{k} = {core.fmt(p['value'])} {p['unit']}" + (f"   = {p['expr']}" if p.get("expr") else ""),
                              "param", k, p.get("note", "") + "   double-click to change")
        for i, f in enumerate(m["features"]):
            ys[3] = self.node(3, ys[3], f["name"], "feat", str(i), f.get("note", ""))
        for i, q in enumerate(m["questions"]):
            ys[4] = self.node(4, ys[4], "? " + q, "note", f"q{i}", "double-click to answer")
        for i, d in enumerate(m["decisions"][-10:]):
            ys[4] = self.node(4, ys[4], "✓ " + d, "note", f"d{i}")
        if not any(y > 34 for y in ys):
            self.create_text(20, 60, anchor="nw", fill=C["muted"], font=FONT,
                             text="The Part Map fills in as the AI researches and designs: requirements → facts → parameters → features.")
        # edges: feature → parameters it uses; parameter → facts its equation references
        for i, f in enumerate(m["features"]):
            for p in f.get("params", []):
                self.edge(("feat", str(i)), ("param", p))
        for k, p in m["params"].items():
            for fk in m["facts"]:
                if p.get("expr") and fk in p["expr"]:
                    self.edge(("param", k), ("fact", fk))
        self.tag_lower("edge")
        self.configure(scrollregion=(0, 0, 20 + 5 * (self.COLW + self.GAP), max(ys) + 40))

    def edge(self, a, b):
        if a not in self.boxes or b not in self.boxes:
            return
        (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = self.boxes[a], self.boxes[b]
        x0, y0 = (ax0, (ay0 + ay1) / 2) if ax0 > bx0 else (ax1, (ay0 + ay1) / 2)
        x1, y1 = (bx1, (by0 + by1) / 2) if ax0 > bx0 else (bx0, (by0 + by1) / 2)
        mid = (x0 + x1) / 2
        self.create_line(x0, y0, mid, y0, mid, y1, x1, y1, smooth=True, fill=C["line"], width=1,
                         tags=("edge", f"e:{a[0]}:{a[1]}", f"e:{b[0]}:{b[1]}"))

    def _key(self):
        cur = self.find_withtag("current")
        tag = next((t for t in self.gettags(cur[0]) if t.startswith("n:")), "") if cur else ""
        return tag.split(":", 2)[1:] if tag else (None, None)

    def on_enter(self, e):
        kind, key = self._key()
        if kind:
            self.itemconfigure(f"e:{kind}:{key}", fill=C["accent"], width=2)
            if kind == "param":
                self.app.ptable.select(key)

    def on_double(self, e):
        kind, key = self._key()
        proj = self.app.project
        if kind == "param":
            p = proj.params[key]
            new = simpledialog.askstring(key, f"{key} ({p['unit']})   {p.get('note', '')}\nValue or =equation:", parent=self,
                                         initialvalue="=" + p["expr"] if p.get("expr") else core.fmt(p["value"]))
            if new:
                self.app.set_param(key, new, "part map")
        elif kind == "note" and key.startswith("q"):
            q = proj.memory["questions"][int(key[1:])]
            ans = simpledialog.askstring("Answer", q, parent=self)
            if ans:
                proj.memory["questions"].remove(q)
                self.app.send(f"Answer to your question \"{q}\": {ans}")
        elif kind == "fact":
            f = proj.memory["facts"][key]
            messagebox.showinfo(key, f"{key} = {f['value']} {f['unit']}\nSource: {f.get('source') or 'unknown'}", parent=self)
        elif kind == "req":
            self.app.edit_brief()


# ============================================================================ main window
class App(tk.Tk):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings or core.Settings.load()
        self.project, self.report, self.last_report = None, None, None
        self.q = queue.Queue()
        self.busy_llm = self.building = self.build_again = False
        self.build_source, self.build_timer, self.pending_send = "user", None, None
        self.undo_stack, self.redo_stack = [], []
        self.dirty, self.llm_ok, self.auto_turns = False, False, 0
        self.stop_evt = threading.Event()
        self.code_mtime = 0.0
        self.stream_start = None

        self.title(core.APP_NAME)
        self.geometry("1440x880")
        self.minsize(1080, 660)
        self.configure(bg=C["bg"])
        try:
            self.iconbitmap(default=str(core.HERE / "icon.ico"))
        except tk.TclError:
            pass
        self._style()
        self._menu()
        self._ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.report_callback_exception = self._on_error
        for seq, fn in (("<Control-n>", self.new_project), ("<Control-o>", self.open_project), ("<Control-s>", self.save),
                        ("<Control-p>", self.quick_send), ("<F5>", self.build_now), ("<Control-z>", self.undo),
                        ("<Control-y>", self.redo)):
            self.bind_all(seq, lambda e, fn=fn: self._shortcut(e, fn))
        self.after(40, self._pump)
        self.after(30_000, self._autosave)
        self.after(1500, self._watch_code)
        self.check_ai()
        self.show_welcome()

    # --------------------------------------------------------------- look
    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=C["panel"], foreground=C["ink"], font=FONT)
        st.configure("TFrame", background=C["panel"])
        st.configure("Tool.TFrame", background=C["bg"])
        st.configure("TLabel", background=C["panel"])
        st.configure("Muted.TLabel", foreground=C["muted"], font=SMALL)
        st.configure("Cap.TLabel", foreground=C["muted"], font=("Segoe UI", 8, "bold"))
        st.configure("H1.TLabel", font=("Segoe UI", 15, "bold"))
        st.configure("H2.TLabel", font=("Segoe UI", 11, "bold"))
        st.configure("Big.TLabel", font=("Segoe UI", 26, "bold"), background=C["bg"])
        st.configure("Welcome.TFrame", background=C["bg"])
        st.configure("WMuted.TLabel", background=C["bg"], foreground=C["muted"])
        st.configure("Status.TLabel", background=C["bg"], foreground=C["muted"], font=SMALL, padding=(8, 3))
        st.configure("TButton", padding=(10, 4))
        st.configure("Tool.TButton", padding=(8, 4), background=C["bg"], borderwidth=0)
        st.map("Tool.TButton", background=[("active", "#dfe3dc")])
        st.configure("Accent.TButton", background=C["accent"], foreground="white", borderwidth=0)
        st.map("Accent.TButton", background=[("active", "#0a4d97"), ("disabled", "#9db7d6")])
        st.configure("Send.TButton", background=C["ok"], foreground="white", borderwidth=0, padding=(12, 4), font=BOLD)
        st.map("Send.TButton", background=[("active", "#237032")])
        st.configure("Send.TMenubutton", background=C["ok"], foreground="white", borderwidth=0, arrowcolor="white", padding=(4, 4))
        st.map("Send.TMenubutton", background=[("active", "#237032")])
        st.configure("Tool.TMenubutton", background=C["bg"], borderwidth=0, padding=(8, 4))
        st.configure("TNotebook", background=C["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", padding=(14, 5), font=FONT)
        st.map("TNotebook.Tab", background=[("selected", C["panel"])])
        st.configure("Treeview", rowheight=24, fieldbackground="white")
        st.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _menu(self):
        mb = tk.Menu(self)
        f = tk.Menu(mb, tearoff=0)
        f.add_command(label="New part…", accelerator="Ctrl+N", command=self.new_project)
        f.add_command(label="Open part…", accelerator="Ctrl+O", command=self.open_project)
        self.recent_menu = tk.Menu(f, tearoff=0, postcommand=self._fill_recent)
        f.add_cascade(label="Open recent", menu=self.recent_menu)
        f.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        f.add_command(label="Save revision…", command=self.save_revision)
        self.rev_menu = tk.Menu(f, tearoff=0, postcommand=self._fill_revisions)
        f.add_cascade(label="Revisions", menu=self.rev_menu)
        f.add_separator()
        f.add_command(label="Export drawing (SVG)…", command=self.export_svg)
        f.add_command(label="Import body (STEP) modelled elsewhere…", command=self.import_body)
        f.add_command(label="Show project folder", command=lambda: self.project and os.startfile(self.project.folder))
        f.add_separator()
        f.add_command(label="Exit", command=self.on_close)
        mb.add_cascade(label="File", menu=f)
        e = tk.Menu(mb, tearoff=0)
        e.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        e.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo)
        mb.add_cascade(label="Edit", menu=e)
        p = tk.Menu(mb, tearoff=0)
        p.add_command(label="Build", accelerator="F5", command=self.build_now)
        p.add_command(label="Edit brief…", command=self.edit_brief)
        p.add_command(label="Research again", command=lambda: self.run_research())
        p.add_command(label="Ask the AI to review the design", command=lambda: self.send(
            "Review the current design against the brief, research and build report. Fix anything wrong."))
        mb.add_cascade(label="Part", menu=p)
        self.send_menu = tk.Menu(mb, tearoff=0, postcommand=self._fill_send_menu)
        mb.add_cascade(label="Send", menu=self.send_menu)
        mb.add_command(label="Settings", command=self.open_settings)
        h = tk.Menu(mb, tearoff=0)
        h.add_command(label="Getting started", command=self.help)
        h.add_command(label="Open log folder", command=lambda: os.startfile(core.HOME))
        h.add_command(label=f"About {core.APP_NAME}", command=lambda: messagebox.showinfo(
            "About", f"{core.APP_NAME} {core.VERSION}\nPrompt-engineer parts with a local AI, FreeCAD and an editable drawing.", parent=self))
        mb.add_cascade(label="Help", menu=h)
        self.configure(menu=mb)

    def _ui(self):
        tb = ttk.Frame(self, style="Tool.TFrame", padding=(6, 4))
        tb.pack(fill="x")
        for text, cmd in (("＋ New", self.new_project), ("Open", self.open_project), ("Save", self.save), ("|", None),
                          ("↶ Undo", self.undo), ("↷ Redo", self.redo), ("|", None), ("⟳ Build  F5", self.build_now)):
            if text == "|":
                ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6, pady=3)
            else:
                ttk.Button(tb, text=text, style="Tool.TButton", command=cmd).pack(side="left")
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6, pady=3)
        self.send_btn = ttk.Button(tb, text="▶ Send", style="Send.TButton", command=self.quick_send)
        self.send_btn.pack(side="left")
        self.send_pick = ttk.Menubutton(tb, style="Send.TMenubutton", width=0)
        self.send_pick_menu = tk.Menu(self.send_pick, tearoff=0, postcommand=lambda: self._fill_send_menu(self.send_pick_menu))
        self.send_pick.configure(menu=self.send_pick_menu)
        self.send_pick.pack(side="left", padx=(1, 8))
        self.hand = ttk.Menubutton(tb, text="✎ Model by hand", style="Tool.TMenubutton")
        self.hand_menu = tk.Menu(self.hand, tearoff=0, postcommand=self._fill_hand_menu)
        self.hand.configure(menu=self.hand_menu)
        self.hand.pack(side="left")
        ttk.Button(tb, text="⚙ Settings", style="Tool.TButton", command=self.open_settings).pack(side="right")
        self.refresh_send()

        self.main = ttk.PanedWindow(self, orient="horizontal")
        self.main.pack(fill="both", expand=True)
        self.nb = ttk.Notebook(self.main)
        draw_pane = ttk.PanedWindow(self.nb, orient="vertical")
        self.drawing = DrawingCanvas(draw_pane, self)
        self.ptable = ParamTable(draw_pane, self)
        draw_pane.add(self.drawing, weight=5)
        draw_pane.add(self.ptable, weight=1)
        self.nb.add(draw_pane, text="Drawing")
        self.map = MapCanvas(self.nb, self)
        self.nb.add(self.map, text="Part Map")
        self.nb.add(self._research_tab(), text="Research")
        self.nb.add(self._code_tab(), text="Model code")
        self.main.add(self.nb, weight=3)
        self.main.add(self._chat_pane(), weight=1)

        sb = ttk.Frame(self, style="Tool.TFrame")
        sb.pack(fill="x", side="bottom")
        self.st_ai = ttk.Label(sb, style="Status.TLabel", text="AI: checking…")
        self.st_ai.pack(side="left")
        self.st_ctx = ttk.Label(sb, style="Status.TLabel")
        self.st_ctx.pack(side="left")
        self.st_build = ttk.Label(sb, style="Status.TLabel")
        self.st_build.pack(side="right")

        self.welcome = ttk.Frame(self, style="Welcome.TFrame")

    def _research_tab(self):
        f = ttk.Frame(self.nb, padding=10)
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.search_var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.search_var, font=FONT)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self.manual_search())
        ttk.Button(top, text="Search the web", command=self.manual_search).pack(side="left", padx=6)
        ttk.Button(top, text="Edit brief…", command=self.edit_brief).pack(side="left")
        pw = ttk.PanedWindow(f, orient="horizontal")
        pw.pack(fill="both", expand=True, pady=(8, 0))
        left = ttk.Frame(pw)
        ttk.Label(left, text="SOURCES  ·  double-click to open", style="Cap.TLabel").pack(anchor="w")
        self.src_tree = ttk.Treeview(left, columns=("url",), show="tree headings")
        self.src_tree.heading("#0", text="Title", anchor="w")
        self.src_tree.heading("url", text="Address", anchor="w")
        self.src_tree.column("#0", width=260)
        self.src_tree.pack(fill="both", expand=True)
        self.src_tree.bind("<Double-1>", lambda e: self.src_tree.selection() and webbrowser.open(
            self.src_tree.item(self.src_tree.selection()[0], "values")[0]))
        right = ttk.Frame(pw)
        ttk.Label(right, text="NOTES  ·  the AI reads these; edit freely", style="Cap.TLabel").pack(anchor="w")
        self.notes = ScrolledText(right, wrap="word", font=FONT, relief="flat", padx=8, pady=6, undo=True)
        self.notes.pack(fill="both", expand=True)
        self.notes.bind("<KeyRelease>", lambda e: self._notes_changed())
        pw.add(left, weight=1)
        pw.add(right, weight=2)
        return f

    def _code_tab(self):
        f = ttk.Frame(self.nb, padding=10)
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, style="Muted.TLabel", text="Parametric FreeCAD source. Edit here or in any editor (Model by hand ▸ "
                  "Edit source): saved changes rebuild automatically.").pack(side="left")
        ttk.Button(top, text="Apply and build  F5", style="Accent.TButton", command=self.build_now).pack(side="right")
        self.code = ScrolledText(f, wrap="none", font=MONO, relief="flat", padx=8, pady=6, undo=True, tabs=("1c",))
        self.code.pack(fill="both", expand=True)
        return f

    def _chat_pane(self):
        f = ttk.Frame(self.main, padding=(8, 8, 8, 8))
        head = ttk.Frame(f)
        head.pack(fill="x")
        ttk.Label(head, text="Design assistant", style="H2.TLabel").pack(side="left")
        self.model_lbl = ttk.Label(head, style="Muted.TLabel")
        self.model_lbl.pack(side="right")
        self.chat = ScrolledText(f, wrap="word", font=FONT, relief="flat", padx=10, pady=8, state="disabled", width=44)
        self.chat.pack(fill="both", expand=True, pady=(6, 6))
        for tag, color, font in (("you_h", C["accent"], BOLD), ("ai_h", C["ok"], BOLD), ("sys_h", C["muted"], ("Segoe UI", 9, "bold")),
                                 ("err_h", C["red"], BOLD), ("sys", C["muted"], SMALL), ("err", C["red"], FONT)):
            self.chat.tag_configure(tag, foreground=color, font=font)
        self.chat.tag_configure("you", lmargin1=0, lmargin2=0)
        self.inp = tk.Text(f, height=4, wrap="word", font=FONT, relief="solid", bd=1, padx=6, pady=4, highlightthickness=0)
        self.inp.pack(fill="x")
        self.inp.bind("<Control-Return>", lambda e: (self.send(), "break")[1])
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Ctrl+Enter to send", style="Muted.TLabel").pack(side="left")
        self.send_chat_btn = ttk.Button(row, text="Send", style="Accent.TButton", command=self.send)
        self.send_chat_btn.pack(side="right")
        self.stop_btn = ttk.Button(row, text="Stop", command=self.stop_evt.set, state="disabled")
        self.stop_btn.pack(side="right", padx=6)
        return f

    # --------------------------------------------------------------- welcome
    def show_welcome(self):
        w = self.welcome
        for c in w.winfo_children():
            c.destroy()
        inner = ttk.Frame(w, style="Welcome.TFrame")
        inner.place(relx=0.5, rely=0.42, anchor="center")
        ttk.Label(inner, text=core.APP_NAME, style="Big.TLabel").pack(anchor="w")
        ttk.Label(inner, text="Describe a part. A local AI researches it and designs it in FreeCAD.\n"
                  "Tweak it on the drawing. Send it to your slicer.", style="WMuted.TLabel", font=("Segoe UI", 11),
                  justify="left").pack(anchor="w", pady=(4, 18))
        row = ttk.Frame(inner, style="Welcome.TFrame")
        row.pack(anchor="w")
        ttk.Button(row, text="＋  New part", style="Accent.TButton", command=self.new_project).pack(side="left")
        ttk.Button(row, text="Open part…", command=self.open_project).pack(side="left", padx=8)
        ttk.Label(inner, text="SETUP", style="WMuted.TLabel", font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(22, 4))
        self.setup_lbl = ttk.Label(inner, style="WMuted.TLabel", font=FONT, justify="left")
        self.setup_lbl.pack(anchor="w")
        self._setup_text()
        recent = [r for r in self.settings["recent"] if (Path(r) / "project.json").exists()]
        if recent:
            ttk.Label(inner, text="RECENT", style="WMuted.TLabel", font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(22, 4))
            for r in recent[:6]:
                lbl = ttk.Label(inner, text=f"{Path(r).name}    {r}", style="WMuted.TLabel", cursor="hand2", font=FONT)
                lbl.pack(anchor="w", pady=1)
                lbl.bind("<Button-1>", lambda e, r=r: self.open_project(r))
        w.place(x=0, y=0, relwidth=1, relheight=1)
        w.lift()

    # --------------------------------------------------------------- threading
    def bg(self, fn, done=None, fail=None):
        def run():
            try:
                self.q.put((done, fn()))
            except Exception as e:  # noqa: surfaced to the user by the fail handler
                if isinstance(e, core.LLMError):
                    core.log.warning("AI: %s", e)
                else:
                    core.log.exception("background task failed")
                self.q.put((fail or self._bg_fail, e))
        threading.Thread(target=run, daemon=True).start()

    def _pump(self):
        try:
            while True:
                cb, val = self.q.get_nowait()
                if cb:
                    cb(val)
        except queue.Empty:
            pass
        self.after(40, self._pump)

    def _bg_fail(self, e):
        self.say("PartForge", f"{type(e).__name__}: {e}", "err")

    def _on_error(self, exc, val, tb):
        core.log.error("UI error: %s", "".join(traceback.format_exception(exc, val, tb)))
        messagebox.showerror(core.APP_NAME, f"Something went wrong: {val}\n\nDetails are in the log (Help ▸ Open log folder).", parent=self)

    def _shortcut(self, e, fn):
        cls = e.widget.winfo_class() if hasattr(e.widget, "winfo_class") else ""
        if fn in (self.undo, self.redo) and cls in ("Text", "Entry", "TEntry"):
            return None   # text boxes keep their own undo
        fn()
        return "break"

    # --------------------------------------------------------------- AI status
    def check_ai(self):
        self.bg(lambda: core.ensure_server(self.settings), self._ai_ok, self._ai_down)

    def _ai_ok(self, models):
        self.llm_ok = bool(models)
        if models and self.settings["model"] not in models:
            self.settings["model"] = models[0]
            self.settings.save()
        name = self.settings["model"] or "no model installed"
        self.st_ai.configure(text=f"AI ● {name}" if models else "AI ○ server up, no model: ollama pull qwen2.5-coder:7b")
        self.model_lbl.configure(text=name)
        self._setup_text()

    def _setup_text(self):
        if not getattr(self, "setup_lbl", None) or not self.setup_lbl.winfo_exists():
            return
        fc = core.find_freecad(self.settings["freecad_cmd"])
        send = apps.quick_app(self.settings)
        lines = [f"✓  FreeCAD   {fc}" if fc else "✗  FreeCAD not found: install it from freecad.org (free)",
                 f"✓  Local AI   {self.settings['model']}" if self.llm_ok else
                 "✗  Local AI offline: install Ollama (ollama.com), then run   ollama pull qwen2.5-coder:7b",
                 f"✓  Quick send   {send['name']}" if send else "–  No slicer found (optional): add one in Send ▸ Manage apps"]
        self.setup_lbl.configure(text="\n".join(lines))

    def _ai_down(self, e):
        self.llm_ok = False
        self._setup_text()
        self.st_ai.configure(text="AI ○ offline (Settings)")
        self.model_lbl.configure(text="offline")

    # --------------------------------------------------------------- project lifecycle
    def new_project(self):
        res = IntakeDialog(self).run()
        if not res:
            return
        self.save()
        brief, research, template = res
        proj = core.Project.create(self.settings["projects_dir"], brief, template or core.STARTER)
        self.first_turn = core.FIRST_TURN_TEMPLATE if template else core.FIRST_TURN
        self.load_project(proj)
        if template:
            self.request_build("user", 0)
        self.say("PartForge", f"Created {proj.folder}.", "sys")
        if research and self.settings["web_search"]:
            self.run_research(then_design=True)
        elif self.llm_ok:
            self.send(self.first_turn, auto=True)
        else:
            self.say("PartForge", NO_AI_HELP.format(url=self.settings["base_url"]) +
                     "\n\nMeanwhile, here's a starter plate you can edit on the drawing.", "sys")
            self.request_build("user", 0)

    def open_project(self, path=None):
        if not path:
            path = filedialog.askopenfilename(parent=self, title="Open part", initialdir=self.settings["projects_dir"],
                                              filetypes=[("PartForge part", "project.json")])
            if not path:
                return
        try:
            proj = core.Project.open(path)
        except (OSError, ValueError, KeyError) as e:
            messagebox.showerror("Open part", f"Couldn't open {path}:\n{e}", parent=self)
            return
        self.save()
        self.load_project(proj)
        if not self.report:
            self.request_build("user", 0)

    def load_project(self, proj):
        self.project = proj
        self.undo_stack, self.redo_stack = [], []
        self.auto_turns = 0
        rep = proj.last_report()
        self.report = rep if rep and rep.get("ok") else None
        self.last_report = rep
        self.code_mtime = proj.model_path.stat().st_mtime if proj.model_path.exists() else 0
        self.settings.add_recent(proj.folder)
        self.settings.save()
        self.welcome.place_forget()
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        for m in proj.data["chat"][-40:]:
            who, tag = {"user": ("You", "you"), "assistant": ("PartForge AI", "ai")}.get(m["role"], ("PartForge", "sys"))
            self.say(who, m["content"], tag, store=False)
        self.drawing.autofit = True
        self.dirty = False
        self.refresh_all(code=True)

    def save(self):
        if not self.project:
            return
        self._pull_code_tab()
        self.project.save()
        self.code_mtime = self.project.model_path.stat().st_mtime
        self.dirty = False
        self.settings.save()
        self._title()
        self.st_build.configure(text=f"Saved {time.strftime('%H:%M:%S')}")

    def _autosave(self):
        if self.dirty and not self.busy_llm:
            self.save()
        self.after(30_000, self._autosave)

    def on_close(self):
        try:
            self.save()
        except OSError as e:
            if not messagebox.askyesno("Save failed", f"Couldn't save: {e}\nClose anyway?", parent=self):
                return
        self.stop_evt.set()
        self.destroy()

    def mark_dirty(self):
        self.dirty = True
        self._title()

    def _title(self):
        name = self.project.name if self.project else ""
        self.title(f"{name}{' •' if self.dirty else ''} — {core.APP_NAME}" if name else core.APP_NAME)

    def edit_brief(self):
        if not self.project:
            return
        res = IntakeDialog(self, self.project.data["brief"], editing=True).run()
        if res:
            self.project.data["brief"], research, _ = res
            self.mark_dirty()
            self.refresh_all()
            if research:
                self.run_research()

    def import_body(self):
        if not self.project:
            return
        p = filedialog.askopenfilename(parent=self, title="Import a body modelled elsewhere",
                                       filetypes=[("STEP / IGES / BREP", "*.step *.stp *.iges *.igs *.brep")])
        if not p:
            return
        dest = self.project.folder / "imports"
        dest.mkdir(exist_ok=True)
        shutil.copy2(p, dest / Path(p).name)
        self.say("PartForge", f"Imported {Path(p).name}. The AI can build on it, e.g. \"add mounting holes to "
                 f"{Path(p).name}\" (it uses load(\"{Path(p).name}\")).", "sys")
        self.request_build("user", 0)

    # --------------------------------------------------------------- refresh
    def refresh_all(self, code=False):
        if not self.project:
            return
        self._title()
        self.ptable.refresh()
        self.map.redraw()
        self.drawing.redraw()
        if code:
            self.code.delete("1.0", "end")
            self.code.insert("1.0", self.project.code)
            self.code.edit_modified(False)
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", self.project.data["research"]["notes"])
        self.src_tree.delete(*self.src_tree.get_children())
        for i, s in enumerate(self.project.data["research"]["sources"], start=1):
            self.src_tree.insert("", "end", text=f"[{i}] {s['title']}", values=(s["url"],))
        msgs = core.build_messages(self.project, self.settings, self.report)
        used = sum(core.est_tokens(m["content"]) for m in msgs)
        self.st_ctx.configure(text=f"context ≈ {used / 1000:.1f}k / {self.settings['context_tokens'] // 1000}k tokens")

    def _notes_changed(self):
        if self.project:
            self.project.data["research"]["notes"] = self.notes.get("1.0", "end-1c")
            self.mark_dirty()

    def prefill(self, text):
        self.inp.delete("1.0", "end")
        self.inp.insert("1.0", text)
        self.inp.focus_set()

    # --------------------------------------------------------------- parameters (the heart)
    def snapshot(self):
        return {"params": copy.deepcopy(self.project.params), "code": self.project.code}

    def restore(self, snap):
        self.project.memory["params"] = copy.deepcopy(snap["params"])
        self.project.code = snap["code"]

    def push_undo(self, snap=None):
        self.undo_stack = (self.undo_stack + [snap or self.snapshot()])[-100:]
        self.redo_stack.clear()

    def set_param(self, name, text, source):
        """One path for every edit (drawing, table, Part Map): validate, re-solve equations, rebuild, tell the AI."""
        proj = self.project
        p = proj.params.get(name)
        if not p:
            return False
        try:
            value, expr = core.parse_entry(text)
        except ValueError as e:
            messagebox.showwarning("Dimension", str(e), parent=self)
            return False
        snap, old = self.snapshot(), p["value"]
        p["expr"] = expr
        if not expr:
            p["value"] = value
        try:
            core.resolve_params(proj.memory)
            for k, q in proj.params.items():
                if q.get("min") is not None and q.get("max") is not None and not q["min"] <= q["value"] <= q["max"]:
                    raise ValueError(f"{k} would be {core.fmt(q['value'])} {q['unit']}; its range is "
                                     f"{core.fmt(q['min'])} – {core.fmt(q['max'])}. Ask the AI to widen it if you need to.")
        except ValueError as e:
            self.restore(snap)
            messagebox.showwarning("Dimension", str(e), parent=self)
            return False
        if abs(p["value"] - old) < 1e-12 and expr == snap["params"][name].get("expr", ""):
            return True
        self.push_undo(snap)
        proj.log_change(f"{name}: {core.fmt(old)} → {core.fmt(p['value'])} {p['unit']} (user, {source})")
        self.mark_dirty()
        self.refresh_all()
        self.request_build("user")
        return True

    def undo(self):
        if self.project and self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.restore(self.undo_stack.pop())
            self._after_restore("Undo")

    def redo(self):
        if self.project and self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self.restore(self.redo_stack.pop())
            self._after_restore("Redo")

    def _after_restore(self, what):
        self.project.log_change(what)
        self.mark_dirty()
        self.refresh_all(code=True)
        self.request_build("user", 0)

    # --------------------------------------------------------------- redlines
    def add_redline(self, view, x, y, text):
        a, b = VIEW_AXES[view]
        marks = self.project.data["markups"]
        rid = max((m["id"] for m in marks), default=0) + 1
        where = f"{a}={core.fmt(round(x, 1))}, {b}={core.fmt(round(y, 1))} mm"
        marks.append({"id": rid, "view": view, "x": x, "y": y, "where": where, "text": text, "status": "open"})
        self.mark_dirty()
        self.drawing.redraw()
        self.send(f"Redline #{rid} on the {VIEW_LABEL[view]} view near {where}: {text}")

    def set_redline(self, rid, status):
        for m in self.project.data["markups"]:
            if m["id"] == rid:
                m["status"] = status
        self.mark_dirty()
        self.drawing.redraw()

    # --------------------------------------------------------------- build
    def _pull_code_tab(self):
        """Code tab edits become the model (user code: syntax check only)."""
        if not self.project or not self.code.edit_modified():
            return True
        text = self.code.get("1.0", "end-1c").rstrip() + "\n"
        self.code.edit_modified(False)
        if text == self.project.code:
            return True
        err = core.check_code(text, strict=False)
        if err:
            messagebox.showwarning("Model code", err, parent=self)
            return False
        self.push_undo()
        self.project.set_code(text)
        self.project.log_change("model.py edited by hand")
        self.mark_dirty()
        return True

    def build_now(self):
        if self.project and self._pull_code_tab():
            self.refresh_all()
            self.request_build("user", 0)

    def request_build(self, source="user", delay=350):
        self.build_source = source
        if self.build_timer:
            self.after_cancel(self.build_timer)
        self.build_timer = self.after(delay, self._start_build)

    def _start_build(self):
        self.build_timer = None
        if not self.project:
            return
        if self.building:
            self.build_again = True
            return
        fc = core.find_freecad(self.settings["freecad_cmd"])
        if not fc:
            self.say("PartForge", "FreeCAD wasn't found. Install FreeCAD (freecad.org) or set its path in Settings.", "err")
            return
        self.building = True
        self.drawing.busy = True
        self.drawing.redraw()
        self.st_build.configure(text="Building…")
        proj, source = self.project, self.build_source
        code, values = proj.code, proj.values()
        self.bg(lambda: core.run_build(fc, proj.folder, code, values), lambda rep: self._build_done(rep, proj, source))

    def _build_done(self, rep, proj, source):
        self.building = False
        self.drawing.busy = False
        if proj is not self.project:
            return
        self.last_report = rep
        if rep.get("ok"):
            self.report = rep
            proj.data["builds"] += 1
            probs = core.report_problems(rep, proj.params, self.settings["bed"])
            self.st_build.configure(text=f"Built in {rep.get('seconds', 0):.1f} s" + (f"  ·  ⚠ {len(probs)} check(s)" if probs else "  ·  checks passed"))
            if source == "ai":
                for m in proj.data["markups"]:
                    if m["status"] == "open":
                        m["status"] = "addressed"
                if probs:
                    self.auto_ask("The build succeeded but has problems: " + "; ".join(probs) + ". Fix them in model.py.")
            if self.pending_send:
                app, self.pending_send = self.pending_send, None
                self.send_to(app)
        else:
            err = rep.get("error", "").strip()
            self.st_build.configure(text="Build failed")
            self.say("FreeCAD", err.splitlines()[-1] if err else "Build failed.", "err")
            self.pending_send = None
            changes = "; ".join(proj.memory["changes"][-3:])
            if source == "ai":
                self.auto_ask(f"Your model.py failed to build:\n{err[-1500:]}\nFix it.")
            else:
                self.auto_ask(f"The user just changed the design ({changes}) and FreeCAD now fails:\n{err[-1500:]}\n"
                              "Update model.py so the design works with the user's new values. Don't revert them.")
        self.mark_dirty()
        self.refresh_all()
        if self.build_again:
            self.build_again = False
            self.request_build(self.build_source, 0)

    # --------------------------------------------------------------- external model.py edits
    def _watch_code(self):
        try:
            p = self.project.model_path if self.project else None
            if p and p.exists() and p.stat().st_mtime > self.code_mtime + 0.01:
                self.code_mtime = p.stat().st_mtime
                text = p.read_text("utf-8")
                if text != self.project.code:
                    err = core.check_code(text, strict=False)
                    if err:
                        self.say("PartForge", f"model.py changed outside PartForge but has an error: {err}", "err")
                    else:
                        self.push_undo()
                        self.project.set_code(text)
                        self.project.log_change("model.py edited in an external editor")
                        self.say("PartForge", "model.py changed in your editor. Rebuilding.", "sys")
                        self.refresh_all(code=True)
                        self.request_build("user", 0)
        except OSError:
            pass
        self.after(1500, self._watch_code)

    # --------------------------------------------------------------- send to apps
    def refresh_send(self):
        a = apps.quick_app(self.settings)
        self.send_btn.configure(text=f"▶ Send to {a['name']}" if a else "▶ Send to… (set up)")

    def _fill_send_menu(self, menu=None):
        menu = menu or self.send_menu
        menu.delete(0, "end")
        all_apps = apps.all_apps(self.settings)
        quick = apps.quick_app(self.settings)
        menu.add_command(label=f"Send to {quick['name']}" if quick else "Send (no app set)", accelerator="Ctrl+P",
                         command=self.quick_send)
        menu.add_separator()
        self._quick_var = tk.StringVar(value=quick["id"] if quick else "")
        for kind, label in apps.KINDS.items():
            items = [a for a in all_apps if a["kind"] == kind and a["path"]]
            if not items:
                continue
            menu.add_command(label=label.upper(), state="disabled")
            for a in items:
                menu.add_radiobutton(label=a["name"], variable=self._quick_var, value=a["id"],
                                     command=lambda a=a: self.set_quick(a))
        menu.add_separator()
        menu.add_command(label="Manage apps…", command=lambda: AppsDialog(self, self.settings).run())

    def _fill_hand_menu(self):
        m = self.hand_menu
        m.delete(0, "end")
        for a in apps.all_apps(self.settings):
            if a["kind"] in ("cad", "editor") and a["path"]:
                verb = "Edit source in" if a["kind"] == "editor" else "Open in"
                m.add_command(label=f"{verb} {a['name']}", command=lambda a=a: self.send_to(a))
        m.add_separator()
        m.add_command(label="Import a body modelled elsewhere (STEP)…", command=self.import_body)
        m.add_command(label="Manage apps…", command=lambda: AppsDialog(self, self.settings).run())

    def set_quick(self, a):
        self.settings["quick_send"] = a["id"]
        self.settings.save()
        self.refresh_send()
        self.st_build.configure(text=f"Quick send → {a['name']}")

    def quick_send(self):
        a = apps.quick_app(self.settings)
        if not a:
            AppsDialog(self, self.settings).run()
            return
        self.send_to(a)

    def send_to(self, a):
        if not self.project:
            return
        self.save()
        if a["sends"] != "code" and (self.building or self.build_timer):
            self.pending_send = a    # finish the build first, then hand over the fresh files
            self.st_build.configure(text=f"Will send to {a['name']} after this build…")
            return
        if a["sends"] != "code" and self.last_report and not self.last_report.get("ok"):
            messagebox.showwarning("Send", "The last build failed; fix it before sending.", parent=self)
            return
        try:
            msg = apps.send(a, self.project.folder, self.report)
        except (apps.SendError, OSError) as e:
            messagebox.showwarning("Send", str(e), parent=self)
            return
        self.st_build.configure(text=msg)

    # --------------------------------------------------------------- chat / AI
    def say(self, who, text, tag="sys", store=True):
        self.chat.configure(state="normal")
        self.chat.insert("end", who + "\n", (tag + "_h",))
        self.chat.insert("end", text.strip() + "\n\n", (tag,))
        self.chat.configure(state="disabled")
        self.chat.see("end")
        if store and self.project and tag in ("sys", "err"):
            self.project.chat_add("note", text)

    def auto_ask(self, text):
        """AI follow-ups the app starts itself (repairs, research follow-through). Capped per user message."""
        if not self.settings["auto_fix"] or not self.llm_ok:
            return
        if self.auto_turns >= 3:
            self.say("PartForge", "Paused automatic repairs after 3 tries. Tell the AI how to proceed.", "sys")
            return
        self.auto_turns += 1
        self.send(text, auto=True)

    def send(self, text=None, auto=False):
        if not self.project:
            return
        if text is None:
            text = self.inp.get("1.0", "end").strip()
            if not text:
                return
            self.inp.delete("1.0", "end")
        if self.busy_llm:
            self.after(500, lambda: self.send(text, auto))
            return
        if not auto:
            self.auto_turns = 0
        self._pull_code_tab()
        self.project.chat_add("user", text, auto=auto)
        shown = text if not auto or len(text) < 160 else text.splitlines()[0][:150] + " …"
        self.say("PartForge ▸ AI" if auto else "You", shown, "sys" if auto else "you", store=False)
        self.busy_llm = True
        self.stop_evt.clear()
        self.stop_btn.configure(state="normal")
        self.send_chat_btn.configure(state="disabled")
        self.chat.configure(state="normal")
        self.chat.insert("end", "PartForge AI\n", ("ai_h",))
        self.stream_start = self.chat.index("end-1c")
        self.chat.insert("end", "…")
        self.chat.configure(state="disabled")
        msgs = core.build_messages(self.project, self.settings, self.report)
        settings, proj = self.settings, self.project
        self.drawing.redraw()
        self.bg(lambda: core.chat(settings, msgs, on_delta=lambda d: self.q.put((self._delta, d)), stop=self.stop_evt),
                lambda reply: self._reply(reply, proj), lambda e: self._reply_failed(e, proj))

    def _delta(self, piece):
        self.chat.configure(state="normal")
        if self.chat.get(self.stream_start, "end-1c") == "…":
            self.chat.delete(self.stream_start, "end-1c")
        self.chat.insert("end", piece)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _end_stream(self, text, tag="ai"):
        self.busy_llm = False
        self.stop_btn.configure(state="disabled")
        self.send_chat_btn.configure(state="normal")
        self.chat.configure(state="normal")
        self.chat.delete(self.stream_start, "end")
        self.chat.insert("end", "\n" + text.strip() + "\n\n", (tag,))
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _reply_failed(self, e, proj):
        self._end_stream(NO_AI_HELP.format(url=self.settings["base_url"]) if isinstance(e, core.LLMError)
                         and "reach" in str(e) else f"{e}", "err")
        if isinstance(e, core.LLMError):
            self.check_ai()

    def _reply(self, reply, proj):
        shown = core.condense(reply) or "(no answer)"
        self._end_stream(shown)
        if proj is not self.project:
            return
        proj.chat_add("assistant", shown)
        self.apply_reply(reply)
        if self.llm_ok:
            settings = self.settings
            self.bg(lambda: core.summarize_old(proj, settings, core.asker(settings)), lambda res: self._summarized(res, proj))

    def _summarized(self, res, proj):
        if res:
            proj.data["summary"], idx = res
            for i in idx:
                proj.data["chat"][i]["archived"] = True
            self.mark_dirty()

    def apply_reply(self, reply):
        proj = self.project
        code, mem, searches = core.parse_reply(reply)
        rebuild = False
        if mem:
            self.push_undo()
            changes = core.merge_memory(proj.memory, mem)
            try:
                core.resolve_params(proj.memory)
            except ValueError as e:
                self.say("PartForge", str(e), "err")
            for c in changes:
                proj.log_change(c + " (AI)")
            rebuild = bool(changes)
        if code:
            err = core.check_code(code)
            if err:
                self.say("PartForge", f"Rejected the AI's model.py: {err}", "err")
                self.auto_ask(f"Your model.py was rejected: {err}. Send a corrected complete model.py.")
            else:
                self.push_undo()
                added, removed = proj.set_code(code)
                if added or removed:
                    self.say("PartForge", "Parameters " + ", ".join([f"+{a}" for a in added] + [f"−{r}" for r in removed]), "sys")
                rebuild = True
        self.mark_dirty()
        self.refresh_all(code=bool(code))
        if rebuild:
            self.request_build("ai", 0)
        if searches:
            self.run_research(searches, follow_up=True)

    # --------------------------------------------------------------- research
    def manual_search(self):
        q = self.search_var.get().strip()
        if q and self.project:
            self.search_var.set("")
            self.run_research([q])

    def run_research(self, queries=None, then_design=False, follow_up=False):
        proj, settings = self.project, self.settings
        ask = core.asker(settings) if self.llm_ok else None
        self.say("PartForge", "Researching the web" + (f": {'; '.join(queries)}" if queries else " from your brief…"), "sys")
        self.st_build.configure(text="Researching…")
        start = len(proj.data["research"]["sources"]) + 1

        def work():
            qs = queries or core.make_queries(proj.data["brief"], ask)
            srcs = core.gather(qs)
            try:
                notes = core.digest(proj.data["brief"], srcs, ask, start) if srcs else ""
            except core.LLMError:
                notes = core.digest(proj.data["brief"], srcs, None, start)
            return qs, srcs, notes
        self.bg(work, lambda res: self._researched(res, proj, then_design, follow_up),
                lambda e: self._research_failed(e, then_design))

    def _researched(self, res, proj, then_design, follow_up):
        qs, srcs, notes = res
        r = proj.data["research"]
        r["queries"] += qs
        r["sources"] += [{k: s[k] for k in ("title", "url", "snippet", "query")} for s in srcs]
        _, mem, _ = core.parse_reply(notes)
        clean = core.BLOCK.sub("", notes).strip()
        if clean:
            r["notes"] = (r["notes"] + "\n\n" if r["notes"] else "") + f"## {'; '.join(qs)}\n{clean}"
        if mem:
            core.merge_memory(proj.memory, mem)
        self.mark_dirty()
        self.refresh_all()
        self.st_build.configure(text=f"Research: {len(srcs)} sources")
        self.say("PartForge", f"Found {len(srcs)} sources" + (f" and {len(mem.get('facts', {}))} facts" if mem else "")
                 + ". See the Research tab.", "sys")
        if then_design:
            self._first_design()
        elif follow_up:
            self.auto_ask("The search results are in your research notes. Continue the design.")

    def _research_failed(self, e, then_design):
        self.say("PartForge", f"Research failed ({e}). Continuing without it.", "err")
        if then_design:
            self._first_design()

    def _first_design(self):
        if self.llm_ok:
            self.send(getattr(self, "first_turn", core.FIRST_TURN), auto=True)
        else:
            self.say("PartForge", NO_AI_HELP.format(url=self.settings["base_url"]) +
                     "\n\nMeanwhile, here's a starter plate you can edit on the drawing.", "sys")
            self.request_build("user", 0)

    # --------------------------------------------------------------- revisions & export
    def save_revision(self, label=None):
        if not self.project:
            return
        revs = self.project.data.setdefault("revisions", [])
        letter = self._rev_letter(len(revs))
        if label is None:
            label = simpledialog.askstring("Save revision", f"Revision {letter}: what changed?", parent=self)
            if label is None:
                return
        self._pull_code_tab()
        revs.append({"rev": letter, "label": label.strip(), "time": time.time(), **copy.deepcopy(self.snapshot())})
        self.project.log_change(f"saved revision {letter}")
        self.save()
        self.drawing.redraw()
        self.st_build.configure(text=f"Saved revision {letter}")

    @staticmethod
    def _rev_letter(n):
        """0 -> A, 25 -> Z, 26 -> AA (drawing revision convention)."""
        s = ""
        n += 1
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    def _fill_revisions(self):
        m = self.rev_menu
        m.delete(0, "end")
        revs = self.project.data.get("revisions", []) if self.project else []
        if not revs:
            m.add_command(label="No revisions yet", state="disabled")
        for rv in reversed(revs):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(rv["time"]))
            m.add_command(label=f"Restore {rv['rev']}   {rv['label']}   ({when})", command=lambda rv=rv: self.restore_revision(rv))

    def restore_revision(self, rv):
        self.push_undo()
        self.restore(rv)
        self._after_restore(f"Restored revision {rv['rev']}")

    def export_svg(self, path=None):
        if not self.project or not self.report:
            return
        path = path or filedialog.asksaveasfilename(parent=self, defaultextension=".svg", initialdir=self.project.out,
                                                    initialfile=f"{self.project.folder.name}-drawing.svg",
                                                    filetypes=[("SVG drawing", "*.svg")])
        if path:
            Path(path).write_text(canvas_to_svg(self.drawing), encoding="utf-8")
            self.st_build.configure(text=f"Exported {Path(path).name}  (open it in a browser to print or save as PDF)")

    # --------------------------------------------------------------- misc
    def _fill_recent(self):
        self.recent_menu.delete(0, "end")
        for r in self.settings["recent"]:
            self.recent_menu.add_command(label=r, command=lambda r=r: self.open_project(r))

    def open_settings(self):
        if SettingsDialog(self, self.settings).run():
            self.check_ai()
            if self.project:
                self.refresh_all()

    def help(self):
        messagebox.showinfo("Getting started", (
            "1. New part: fill in the brief. PartForge researches the web and the AI designs a first version.\n"
            "2. Drawing tab: double-click any blue dimension to change it. Type a value (60, 2.5 in) or an equation "
            "(=width*2). The model rebuilds and everything updates.\n"
            "3. Right-click the drawing to redline a change for the AI. Or just ask in the chat.\n"
            "4. ▶ Send hands the printable STL files to your slicer. Pick the app with the ▾ arrow.\n"
            "5. Model by hand: open the part in FreeCAD or another CAD program, edit model.py in your editor (it "
            "rebuilds on save), or import a STEP body for the AI to build on.\n\n"
            "Everything saves automatically: every 30 seconds and when you close."), parent=self)


def canvas_to_svg(cv):
    """Vector copy of what the drawing canvas shows (lines, text, shapes); prints crisply from any browser."""
    W, H = cv.winfo_width(), cv.winfo_height()
    esc = lambda t: t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="Segoe UI, Arial, sans-serif"><rect width="100%" height="100%" fill="{C["sheet"]}"/>']
    anchors = {"n": ("middle", "hanging"), "s": ("middle", "auto"), "w": ("start", "middle"), "sw": ("start", "auto"),
               "nw": ("start", "hanging"), "center": ("middle", "middle"), "e": ("end", "middle")}
    for item in cv.find_all():
        kind, xy = cv.type(item), cv.coords(item)

        def opt(k):
            return cv.itemcget(item, k)
        if kind == "window" or "tip" in cv.gettags(item):
            continue
        if kind == "line":
            pts = " ".join(f"{xy[i]:.1f},{xy[i + 1]:.1f}" for i in range(0, len(xy), 2))
            dash = f' stroke-dasharray="{opt("dash")}"' if opt("dash") else ""
            out.append(f'<polyline points="{pts}" fill="none" stroke="{opt("fill")}" stroke-width="{opt("width")}"{dash}/>')
            ends = []
            if opt("arrow") in ("first", "both"):
                ends.append((xy[2], xy[3], xy[0], xy[1]))
            if opt("arrow") in ("last", "both"):
                ends.append((xy[-4], xy[-3], xy[-2], xy[-1]))
            for x0, y0, x1, y1 in ends:
                a = math.atan2(y1 - y0, x1 - x0)
                pts = [(x1, y1), (x1 - 9 * math.cos(a) + 3 * math.sin(a), y1 - 9 * math.sin(a) - 3 * math.cos(a)),
                       (x1 - 9 * math.cos(a) - 3 * math.sin(a), y1 - 9 * math.sin(a) + 3 * math.cos(a))]
                out.append(f'<polygon points="{" ".join(f"{u:.1f},{v:.1f}" for u, v in pts)}" fill="{opt("fill")}"/>')
        elif kind in ("rectangle", "oval"):
            x0, y0, x1, y1 = xy
            fill, stroke = opt("fill") or "none", opt("outline") or "none"
            if kind == "rectangle":
                out.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" fill="{fill}" stroke="{stroke}"/>')
            else:
                out.append(f'<ellipse cx="{(x0 + x1) / 2:.1f}" cy="{(y0 + y1) / 2:.1f}" rx="{(x1 - x0) / 2:.1f}" '
                           f'ry="{(y1 - y0) / 2:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{opt("width")}"/>')
        elif kind == "polygon":
            pts = " ".join(f"{xy[i]:.1f},{xy[i + 1]:.1f}" for i in range(0, len(xy), 2))
            out.append(f'<polygon points="{pts}" fill="{opt("fill") or "none"}" stroke="{opt("outline") or "none"}"/>')
        elif kind == "text":
            font = cv.tk.splitlist(opt("font"))
            size = abs(int(font[1])) if len(font) > 1 else 9
            weight = "bold" if "bold" in font else "normal"
            ha, va = anchors.get(opt("anchor"), ("middle", "middle"))
            angle = float(opt("angle") or 0)
            rot = f' transform="rotate({-angle:.0f} {xy[0]:.1f} {xy[1]:.1f})"' if angle else ""
            out.append(f'<text x="{xy[0]:.1f}" y="{xy[1]:.1f}" font-size="{size * 1.33:.1f}" font-weight="{weight}" '
                       f'fill="{opt("fill")}" text-anchor="{ha}" dominant-baseline="{va}"{rot}>{esc(opt("text"))}</text>')
    return "\n".join(out + ["</svg>"])


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    core.setup_logging()
    App().mainloop()


if __name__ == "__main__":
    main()
