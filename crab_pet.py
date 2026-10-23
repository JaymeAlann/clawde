"""Clawde - a pixel-art desktop crab that hangs out on top of your windows
and reacts to what Claude Code is doing.

Run:  pyw crab_pet.py          (no console window)
      py  crab_pet.py --preview out.png   (render every pose to a PNG)
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk

import claude_link
import meetings

__version__ = "0.2.0"
REPO = "JaymeAlann/clawde"
HERE = Path(__file__).resolve().parent
CONFIG_PATH = claude_link.DATA / "crab_config.json"
DEFAULTS = {
    "name": "Clawde",
    "claude_mode": "auto",      # auto | windows | wsl
    "wsl_distro": "Ubuntu",
    "workdir": "",              # Windows folder Claude Code opens in (blank = home); WSL uses ~
    "ai_tips": True,            # ask Claude (haiku) for a tip when you look stuck
    "sleep_after_sec": 180,     # nap after this long with no keyboard/mouse input
    "walk": True,
    "scale": 0,                 # pixel size; 0 = auto from DPI
    "position": None,
    "meeting_notes": True,      # offer to record + transcribe calls (needs requirements-meetings.txt)
    "meetings_folder": "",      # blank = Documents\Clawde Meetings
    "whisper_model": "small",   # tiny | base | small | medium | large-v3
    "meeting_language": "",     # blank = auto-detect, or e.g. "en"
    "keep_audio": False,        # keep the WAV files after transcribing
    "approvals": "bubble",      # bubble = Allow/Deny from Clawde | notify = just tell me | off
    "approval_wait_sec": 60,    # then Claude Code falls back to its own prompt
    "sounds": True,
    "hooks_offered": False,
    "check_updates": True,
    "last_update_check": 0,
}
NO_WINDOW = 0x08000000
KEY = "#fe01fd"                 # transparent colour key

# ---------------------------------------------------------------- config

def load_config():
    cfg = dict(DEFAULTS)
    old = HERE / "crab_config.json"     # versions before 0.2 kept it next to the script
    if not CONFIG_PATH.exists() and old.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    if not CONFIG_PATH.exists():
        save_config(cfg)
    return cfg


def save_config(cfg):
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass

# ---------------------------------------------------------------- win32

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wt.DWORD)]


user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
user32.MonitorFromPoint.restype = ctypes.c_void_p
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
kernel32.GetTickCount.restype = wt.DWORD


def idle_seconds():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    user32.GetLastInputInfo(ctypes.byref(li))
    return ((kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0


def foreground_window():
    h = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, buf, n + 1)
    return h, buf.value


def work_area(x, y):
    hmon = user32.MonitorFromPoint(wt.POINT(int(x), int(y)), 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom


def open_file(path):
    """Open with the default app, or Notepad when nothing is registered for the file type."""
    try:
        os.startfile(path)
    except OSError:
        subprocess.Popen(["notepad.exe", str(path)])


def press_win_h():
    """Start Windows voice typing in whatever field has focus."""
    k = user32.keybd_event
    k(0x5B, 0, 0, 0)
    k(0x48, 0, 0, 0)
    k(0x48, 0, 2, 0)
    k(0x5B, 0, 2, 0)

# ---------------------------------------------------------------- claude cli

class Claude:
    def __init__(self, cfg):
        self.cfg = cfg
        self.mode = None
        self.exe = None
        self.ready = threading.Event()
        threading.Thread(target=self._detect, daemon=True).start()

    @property
    def distro(self):
        return self.cfg.get("wsl_distro") or "Ubuntu"

    def _detect(self):
        want = self.cfg.get("claude_mode", "auto")
        try:
            if want in ("auto", "windows"):
                exe = shutil.which("claude")
                if exe:
                    self.mode, self.exe = "windows", exe
                    return
            if want in ("auto", "wsl") and shutil.which("wsl"):
                r = subprocess.run(["wsl", "-d", self.distro, "--exec", "bash", "-lc", "command -v claude"],
                                   capture_output=True, text=True, timeout=40, creationflags=NO_WINDOW)
                if r.returncode == 0 and r.stdout.strip():
                    self.mode = "wsl"
                    return
            self.mode = "missing"
        except Exception:
            self.mode = "missing"
        finally:
            self.ready.set()

    def workdir(self):
        return self.cfg.get("workdir") or str(Path.home())

    def _argv(self, args):
        if self.mode == "windows":
            return [self.exe, *args]
        return ["wsl", "-d", self.distro, "--exec", "bash", "-lc", 'cd ~ && exec claude "$@"', "claude", *args]

    def ask(self, prompt, session_id=None, model=None, timeout=600):
        """Run `claude -p`. Returns (text, session_id, error)."""
        self.ready.wait(60)
        if self.mode in (None, "missing"):
            return None, session_id, "I couldn't find the Claude Code CLI (set claude_mode in crab_config.json)."
        args = ["-p", "--output-format", "json"]
        if model:
            args += ["--model", model]
        if session_id:
            args += ["--resume", session_id]
        try:
            r = subprocess.run(self._argv(args), input=prompt, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout,
                               cwd=self.workdir(), creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return None, session_id, "Claude took too long to answer."
        except Exception as e:
            return None, session_id, str(e)
        for line in reversed(r.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                sid = data.get("session_id") or session_id
                if data.get("is_error"):
                    return None, sid, data.get("result") or "Claude returned an error."
                return (data.get("result") or "").strip(), sid, None
        return None, session_id, (r.stderr or r.stdout or "No response").strip()[:400]

    def open_terminal(self):
        self.ready.wait(60)
        wt_exe = shutil.which("wt")
        if self.mode == "windows":
            cmd = [self.exe]
            if wt_exe:
                full = [wt_exe, "-w", "new", "new-tab", "--title", "Claude Code", "-d", self.workdir(), *cmd]
            else:
                full = ["cmd", "/c", "start", "Claude Code", "/D", self.workdir(), *cmd]
        elif self.mode == "wsl":
            cmd = ["wsl.exe", "-d", self.distro, "--cd", "~", "--exec", "bash", "-lc", "claude"]
            full = [wt_exe, "-w", "new", "new-tab", "--title", "Claude Code", *cmd] if wt_exe \
                else ["cmd", "/c", "start", "Claude Code", *cmd]
        else:
            return False
        subprocess.Popen(full, creationflags=NO_WINDOW)
        return True

# ---------------------------------------------------------------- activity watcher

class ActivityWatcher(threading.Thread):
    """Watches every Claude Code transcript (Windows + WSL) to tell whether
    any session is thinking or working right now."""

    def __init__(self, distro):
        super().__init__(daemon=True)
        self.distro = distro
        self.status = "idle"
        self.roots = [Path.home() / ".claude" / "projects"]
        self.files = {}         # path -> mtime
        self.cache = {}         # path -> (mtime, kind)
        self.next_list = {}

    def _find_wsl_roots(self):
        for base in (rf"\\wsl.localhost\{self.distro}\home", rf"\\wsl$\{self.distro}\home"):
            try:
                found = [Path(e.path) / ".claude" / "projects" for e in os.scandir(base) if e.is_dir()]
                found = [p for p in found if p.is_dir()]
                if found:
                    self.roots += found
                    return
            except OSError:
                continue

    def _list(self, root):
        out = []
        try:
            for proj in os.scandir(root):
                if not proj.is_dir():
                    continue
                for e in os.scandir(proj.path):
                    if e.name.endswith(".jsonl") and e.is_file():
                        out.append(e.path)
                    elif e.is_dir():
                        sub = os.path.join(e.path, "subagents")
                        if os.path.isdir(sub):
                            out += [s.path for s in os.scandir(sub) if s.name.endswith(".jsonl")]
        except OSError:
            pass
        return out

    @staticmethod
    def _classify(path):
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 65536))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return "idle"
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            kind = rec.get("type")
            msg = rec.get("message") or {}
            content = msg.get("content") if isinstance(msg, dict) else None
            if kind == "user":
                if rec.get("isMeta"):
                    continue
                if isinstance(content, str):
                    if content.startswith("[Request interrupted") or "<local-command" in content:
                        return "idle"
                    if content.startswith("<command-name>"):
                        continue
                    return "thinking"
                if isinstance(content, list):
                    types = [c.get("type") for c in content if isinstance(c, dict)]
                    if "tool_result" in types:
                        return "working"
                    texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
                    if texts and texts[-1].startswith("[Request interrupted"):
                        return "idle"
                    return "thinking"
                continue
            if kind == "assistant" and isinstance(content, list) and content:
                last = content[-1].get("type") if isinstance(content[-1], dict) else None
                return {"tool_use": "working", "thinking": "thinking"}.get(last, "text")
        return "idle"

    def run(self):
        self._find_wsl_roots()
        while True:
            now = time.time()
            for root in self.roots:
                key = str(root)
                if now >= self.next_list.get(key, 0):
                    remote = key.startswith("\\\\")
                    self.next_list[key] = now + (15 if remote else 5)
                    for p in self._list(root):
                        self.files.setdefault(p, 0)
            working = thinking = False
            for p in list(self.files):
                try:
                    m = os.stat(p).st_mtime
                except OSError:
                    self.files.pop(p, None)
                    continue
                self.files[p] = m
                age = now - m
                if age > 900:
                    continue
                cached = self.cache.get(p)
                if not cached or cached[0] != m:
                    cached = (m, self._classify(p))
                    self.cache[p] = cached
                kind = cached[1]
                if (kind == "working" and age < 600) or (kind == "text" and age < 4):
                    working = True
                elif kind == "thinking" and age < 300:
                    thinking = True
            self.status = "working" if working else "thinking" if thinking else "idle"
            time.sleep(1.5)

# ---------------------------------------------------------------- sprite

O, C, H, D = "#4a1608", "#e8572a", "#ff9a6b", "#b8401c"
W, K, P = "#ffffff", "#1b1b1b", "#ff8fab"
BODY_SPANS = [(11, 20), (9, 22), (8, 23), (8, 23), (8, 23), (8, 23), (9, 22), (10, 21)]
LEGS = [(8, 6), (9, 7), (11, 8)]
CLAW_OPEN = ["cc.c.", "cc.cc", "ccccc", "ccccc", ".ccc."]
CLAW_SHUT = [".ccc.", "ccccc", "ccccc", "ccccc", ".ccc."]
# pose -> (claw x, claw y relative to body top, arm cells)
CLAW_POS = {
    "down": (2, 1, [(7, 3)]),
    "mid": (1, -3, [(6, 1), (7, 2)]),
    "up": (1, -8, [(4, -3), (5, -2), (5, -1), (6, 0), (7, 1), (7, 2)]),
    "type": (3, 5, []),
}
ZED = ["XXXXX", "...X.", "..X..", ".X...", "XXXXX"]
ZED_SMALL = ["XXXX", "..X.", ".X..", "XXXX"]
HEART = [".R.R.", "RRRRR", "RRRRR", ".RRR.", "..R.."]


def outline_of(cells):
    out = set()
    for (x, y) in cells:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if n not in cells:
                out.add(n)
    return out


def render(p):
    """Turn pose parameters into {(x, y): colour} on a 32x32 grid."""
    t = p.get("t", 0)
    y0 = 18 + p.get("bob", 0)
    px = {}

    def put(x, y, c):
        if 0 <= x < 32 and 0 <= y < 32:
            px[(x, y)] = c

    def glyph(rows, x, y, colours):
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch in colours:
                    put(x + i, y + j, colours[ch])

    # legs
    legs = p.get("legs", "stand")
    for i, (lx, dy) in enumerate(LEGS):
        for side in (0, 1):
            if legs == "tuck":
                pts = [(lx - 1, dy + 1)]
            elif legs.startswith("dangle"):
                sw = 1 if (i + side + int(legs[-1])) % 2 else 0
                pts = [(lx, dy + 1), (lx, dy + 2), (lx - sw, dy + 3)]
            else:
                lift = 1 if legs.startswith("walk") and (i + side + int(legs[-1])) % 2 == 0 else 0
                pts = [(lx - 1, dy + 1 - lift), (lx - 2, dy + 2 - lift), (lx - 2, dy + 3 - lift)]
            for (x, yy) in pts:
                put(x if side == 0 else 31 - x, y0 + yy, O)

    # body
    body = {}
    for i, (a, b) in enumerate(BODY_SPANS):
        for x in range(a, b + 1):
            c = C
            if i in (1, 2) and a + 1 <= x <= a + 3:
                c = H
            if i == 7 or (i == 6 and x in (a, b)):
                c = D
            body[(x, y0 + i)] = c
    for (x, y) in [(14, 1), (17, 1), (13, 2), (18, 2)]:
        body[(x, y0 + y)] = D
    for c in outline_of(set(body)):
        put(*c, O)
    for c, col in body.items():
        put(*c, col)

    # face
    mouth = p.get("mouth", "smile")
    if mouth == "smile":
        for c in [(14, 3), (15, 4), (16, 4), (17, 3)]:
            put(c[0], y0 + c[1], O)
    elif mouth == "open":
        for c in [(15, 3), (16, 3), (14, 4), (17, 4), (15, 5), (16, 5)]:
            put(c[0], y0 + c[1], O)
        put(15, y0 + 4, "#c2334d")
        put(16, y0 + 4, "#c2334d")
    elif mouth == "o":
        put(15, y0 + 4, O)
        put(16, y0 + 4, O)
    if p.get("blush", True):
        for x in (10, 11, 20, 21):
            put(x, y0 + 3, P)

    # laptop
    if p.get("laptop"):
        scr = {(x, y0 + yy) for x in range(10, 22) for yy in range(0, 8)}
        base = {(x, y0 + yy) for x in range(7, 25) for yy in (8, 9)}
        for c in outline_of(scr | base):
            put(*c, O)
        for c in scr:
            put(*c, "#d9dce4")
        for c in base:
            put(*c, "#a5abb8")
        for c in [(15, 3), (16, 3), (15, 4), (16, 4)]:
            put(c[0], y0 + c[1], C)
        put(15, y0 + 2, H)
        put(16, y0 + 5, D)

    # claws
    limbs = {}
    for side, spec in ((0, p.get("l", ("down", False, 0))), (1, p.get("r", ("down", False, 0)))):
        where, shut, dy = spec
        cx, cy, arm = CLAW_POS[where]
        pattern = CLAW_SHUT if shut else CLAW_OPEN
        for j, row in enumerate(pattern):
            for i, ch in enumerate(row):
                if ch == "c":
                    x = cx + i
                    limbs[(x if side == 0 else 31 - x, y0 + cy + j + dy)] = H if (i == 0 and j in (1, 2)) else C
        for (ax, ay) in arm:
            limbs[(ax if side == 0 else 31 - ax, y0 + ay + dy)] = C
    for c in outline_of(set(limbs)):
        put(*c, O)
    for c, col in limbs.items():
        put(*c, col)

    # eyes on stalks
    kind, look = p.get("eyes", "open"), p.get("look", 0)
    for cx in (12, 19):
        ey = y0 - 5
        put(cx, y0 - 2, O)
        put(cx, y0 - 1, O)
        if kind in ("open", "wide", "sparkle"):
            cells = {(cx + dx, ey + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            for c in outline_of(cells):
                put(*c, O)
            for c in cells:
                put(*c, W)
            if kind == "open":
                put(cx + look, ey, K)
                put(cx + look, ey + 1, K)
            elif kind == "wide":
                put(cx + look, ey, K)
            else:
                put(cx, ey, K)
                put(cx, ey + 1, K)
                put(cx + 1, ey - 1, "#ffd84a")
        elif kind == "closed":
            for dx in (-1, 0, 1):
                put(cx + dx, ey + 1, O)
            put(cx, y0 - 3, O)
        elif kind == "happy":
            put(cx - 1, ey + 1, O)
            put(cx, ey, O)
            put(cx + 1, ey + 1, O)
            put(cx, y0 - 3, O)

    # effects
    fx = p.get("fx", [])
    if "zzz" in fx:
        for k in range(3):
            ph = (t // 4 + k * 5) % 15
            if ph < 12:
                glyph(ZED if k == 0 else ZED_SMALL, 22 + ph // 3, y0 - 9 - ph, {"X": "#7fa8ff"})
    if "heart" in fx:
        glyph(HEART, 13, y0 - 15 + (t // 3) % 2, {"R": "#ff4d6d"})
    if "sparkles" in fx:
        for i, (sx, sy) in enumerate([(3, 4), (27, 3), (2, 13), (28, 12), (16, 1)]):
            if (t + i * 2) % 6 < 3:
                put(sx, sy, "#fff7b0")
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    put(sx + dx, sy + dy, "#ffd84a")
    if "bubbles" in fx:
        for k in range(3):
            ph = (t // 2 + k * 4) % 12
            bx, by = 18 + ph // 3, y0 + 2 - ph
            glyph([".B.", "B.B", ".B."], bx, by, {"B": "#8fd3ff"})
    if "code" in fx:
        for k, cx in enumerate((11, 14, 18, 21)):
            ph = (t + k * 3) % 10
            put(cx, y0 - 2 - ph, "#59e08a")
            if ph % 3 == 0:
                put(cx + 1, y0 - 2 - ph, "#59e08a")
    if "dots" in fx:
        lit = (t // 3) % 4
        for i, cx in enumerate((12, 15, 18)):
            if i < lit:
                glyph(["XX", "XX"], cx, y0 - 12, {"X": W})
    if "bang" in fx:
        bang = {(15, y) for y in range(y0 - 16, y0 - 11)} | {(16, y) for y in range(y0 - 16, y0 - 11)}
        dot = {(15, y0 - 10), (16, y0 - 10)}
        for c in outline_of(bang | dot):
            put(*c, O)
        for c in bang | dot:
            put(*c, "#ffd84a")

    if p.get("rec"):
        # headphones and a blinking REC light
        band = {(x, y0 - 10) for x in range(11, 21)} | {(10, y0 - 9), (21, y0 - 9), (9, y0 - 8), (22, y0 - 8)}
        cups = {(x, y) for x in (8, 9) for y in range(y0 - 7, y0 - 4)} | \
               {(x, y) for x in (22, 23) for y in range(y0 - 7, y0 - 4)}
        for c in outline_of(cups):
            if c not in band:
                put(*c, O)
        for c in band:
            put(*c, "#3b3f4a")
        for c in cups:
            put(*c, "#565c6b")
        if (t // 4) % 2 == 0:
            for c in outline_of({(28, 2), (29, 2), (28, 3), (29, 3)}):
                put(*c, O)
            for c in [(28, 2), (29, 2), (28, 3), (29, 3)]:
                put(*c, "#ff3b3b")

    sx = p.get("shift", 0)
    if sx:
        px = {(x + sx, y): c for (x, y), c in px.items() if 0 <= x + sx < 32}
    return px


def pose_params(name, t, facing=1, look=0, blink=False):
    p = {"t": t, "bob": 0, "eyes": "open", "look": look, "mouth": "smile", "blush": True,
         "l": ("down", False, 0), "r": ("down", False, 0), "legs": "stand", "fx": []}
    if name == "idle":
        p["bob"] = (t // 8) % 2
    elif name == "walk":
        p["legs"] = f"walk{(t // 2) % 2}"
        p["look"] = facing
        p["bob"] = (t // 2) % 2
    elif name == "look":
        p["look"] = look
    elif name == "wave":
        p["eyes"] = "happy"
        p["r"] = ("up" if (t // 3) % 2 else "mid", False, 0)
    elif name == "dance":
        p.update(eyes="happy", mouth="open", bob=-((t // 3) % 2), legs=f"walk{(t // 3) % 2}")
        p["l"] = ("up" if (t // 3) % 2 else "mid", False, 0)
        p["r"] = ("mid" if (t // 3) % 2 else "up", False, 0)
        p["fx"] = ["sparkles"]
    elif name == "bubble":
        p.update(mouth="o", look=1, fx=["bubbles"])
    elif name == "snap":
        p["l"] = ("mid", (t // 2) % 2 == 0, 0)
        p["r"] = ("mid", (t // 2) % 2 == 1, 0)
        p["mouth"] = "open" if (t // 2) % 2 else "smile"
    elif name == "sleep":
        p.update(bob=1 + (t // 10) % 2, eyes="closed", mouth="o", legs="tuck", fx=["zzz"])
        p["l"] = ("down", True, 0)
        p["r"] = ("down", True, 0)
    elif name == "think":
        p.update(bob=-((t // 2) % 2) * 2, eyes="sparkle", mouth="open", fx=["sparkles", "dots"])
        p["l"] = ("up" if (t // 3) % 2 else "mid", (t // 2) % 2 == 0, 0)
        p["r"] = ("mid" if (t // 3) % 2 else "up", (t // 2) % 2 == 1, 0)
    elif name == "work":
        p.update(laptop=True, look=0, fx=["code"], mouth="o")
        p["eyes"] = "open"
        p["l"] = ("type", True, -((t // 1) % 2))
        p["r"] = ("type", True, -((t // 1 + 1) % 2))
    elif name == "drag":
        p.update(eyes="wide", mouth="open", legs=f"dangle{(t // 2) % 2}", blush=False)
        p["l"] = ("up" if (t // 2) % 2 else "mid", False, 0)
        p["r"] = ("mid" if (t // 2) % 2 else "up", False, 0)
    elif name == "happy":
        p.update(eyes="happy", mouth="open", bob=-((t // 2) % 2), fx=["heart"])
        p["l"] = ("up", False, 0)
        p["r"] = ("up", False, 0)
    elif name == "alert":
        p.update(eyes="wide", mouth="open", fx=["bang"])
        p["r"] = ("up" if (t // 3) % 2 else "mid", False, 0)
    elif name == "needs":
        p.update(eyes="wide", mouth="open", bob=-((t // 2) % 2) * 2, fx=["bang"], legs=f"walk{(t // 2) % 2}")
        p["l"] = ("up" if (t // 2) % 2 else "mid", False, 0)
        p["r"] = ("mid" if (t // 2) % 2 else "up", False, 0)
    if blink and p["eyes"] == "open":
        p["eyes"] = "closed"
    return p

# ---------------------------------------------------------------- speech bubble

class Bubble:
    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=KEY)
        self.win.attributes("-transparentcolor", KEY)
        self.cv = tk.Canvas(self.win, bg=KEY, highlightthickness=0, cursor="hand2")
        self.cv.pack()
        self.cv.bind("<Button-1>", self._click)
        self.win.withdraw()
        self.visible = False
        self.until = 0
        self.on_click = None
        self.buttons = []
        self.size = (0, 0)

    def show(self, text, secs=12, on_click=None, actions=None):
        """actions: [(label, callback)] drawn as buttons under the text."""
        cv = self.cv
        cv.delete("all")
        pad = 10
        tid = cv.create_text(pad + 2, pad + 2, text=text, anchor="nw", width=240,
                             font=("Segoe UI", 10), fill="#2a1a14")
        x0, y0, x1, y1 = cv.bbox(tid)
        w, h = x1 + pad, y1 + pad
        self.buttons = []
        if actions:
            bx, by = pad + 2, y1 + 8
            items = []
            for i, (label, cb) in enumerate(actions):
                lid = cv.create_text(bx + 10, by + 5, text=label, anchor="nw",
                                     font=("Segoe UI Semibold", 9), fill="white" if i == 0 else "#2a1a14")
                lx0, ly0, lx1, ly1 = cv.bbox(lid)
                rect = (bx, by, lx1 + 10, ly1 + 5)
                items.append((lid, rect, i == 0))
                self.buttons.append((rect, cb))
                bx = rect[2] + 8
            for lid, rect, primary in items:
                cv.create_rectangle(*rect, fill=C if primary else "#f1e4d8", outline=O, width=1)
                cv.tag_raise(lid)
            w = max(w, bx + pad - 8)
            h = by + (items[0][1][3] - items[0][1][1]) + pad
        cv.create_rectangle(2, 2, w, h, fill="#fffaf3", outline=O, width=2)
        cv.tag_lower(cv.find_all()[-1])
        tx = w // 2
        cv.create_polygon(tx - 8, h - 1, tx + 8, h - 1, tx, h + 9, fill="#fffaf3", outline="")
        cv.create_line(tx - 8, h, tx, h + 9, tx + 8, h, fill=O, width=2)
        if on_click and not actions:
            cv.create_text(w - 6, h - 4, text="click me", anchor="se", font=("Segoe UI", 7), fill="#b8401c")
        cv.tag_raise(tid)
        self.size = (w + 4, h + 12)
        cv.config(width=self.size[0], height=self.size[1])
        self.on_click = on_click
        self.until = time.time() + secs
        self.visible = True
        self.win.deiconify()
        self.follow()
        self.win.lift()

    def hide(self):
        self.visible = False
        self.win.withdraw()

    def follow(self):
        if not self.visible:
            return
        if time.time() > self.until:
            self.hide()
            return
        a = self.app
        rx, ry = a.root.winfo_x(), a.root.winfo_y()
        w, h = self.size
        x = rx + a.W // 2 - w // 2
        y = ry + 6 * a.S - h
        l, t, r, b = work_area(rx + a.W // 2, ry + a.pet_h // 2)
        x = max(l, min(x, r - w))
        y = max(t, y)
        self.win.geometry(f"+{x}+{y}")

    def _click(self, e):
        cb = self.on_click
        for (x0, y0, x1, y1), action in self.buttons:
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                cb = action
                break
        else:
            if self.buttons:
                return
        self.hide()
        if cb:
            cb()

# ---------------------------------------------------------------- chat window

class ChatWindow:
    def __init__(self, app):
        self.app = app
        self.win = None
        self.session_id = None
        self.busy = False
        self.voice = False

    def _build(self):
        a = self.app
        w = self.win = tk.Toplevel(a.root)
        w.title(f"{a.cfg['name']} - chat with Claude Code")
        w.attributes("-topmost", True)
        w.configure(bg="#1c1917")
        w.geometry("420x480")
        w.minsize(320, 300)
        w.protocol("WM_DELETE_WINDOW", self.close)

        top = tk.Frame(w, bg="#1c1917")
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text=f"\U0001F980  {a.cfg['name']}", bg="#1c1917", fg="#ff9a6b",
                 font=("Segoe UI Semibold", 12)).pack(side="left")
        tk.Button(top, text="New chat", command=self.reset, bg="#2b2522", fg="#e7e0db", bd=0,
                  activebackground="#3a322e", activeforeground="white", padx=8).pack(side="right")

        self.log = tk.Text(w, wrap="word", bg="#14110f", fg="#ece6e1", bd=0, padx=10, pady=8,
                           font=("Segoe UI", 10), insertbackground="white", state="disabled")
        self.log.pack(fill="both", expand=True, padx=10)
        self.log.tag_config("you", foreground="#ffb48a", font=("Segoe UI Semibold", 10))
        self.log.tag_config("claude", foreground="#9fd3ff", font=("Segoe UI Semibold", 10))
        self.log.tag_config("err", foreground="#ff7a7a")
        self.log.tag_config("dim", foreground="#8a817b")

        self.status = tk.Label(w, text="Type, or press the mic and talk.", bg="#1c1917", fg="#8a817b",
                               font=("Segoe UI", 9), anchor="w")
        self.status.pack(fill="x", padx=12, pady=(4, 0))

        bar = tk.Frame(w, bg="#1c1917")
        bar.pack(fill="x", padx=10, pady=10)
        self.entry = tk.Entry(bar, bg="#2b2522", fg="white", bd=0, insertbackground="white",
                              font=("Segoe UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 6))
        self.entry.bind("<Return>", lambda e: self.send())
        tk.Button(bar, text="\U0001F3A4", command=self.start_voice, bg="#2b2522", fg="white", bd=0,
                  font=("Segoe UI Emoji", 11), activebackground="#3a322e", width=3).pack(side="left", padx=(0, 6))
        tk.Button(bar, text="Send", command=self.send, bg="#e8572a", fg="white", bd=0,
                  font=("Segoe UI Semibold", 10), activebackground="#ff7a4a", padx=12).pack(side="left")
        self._write(f"{a.cfg['name']}: Hi! Ask me anything and I'll pass it to Claude Code.\n", "dim")

    def _write(self, text, tag=None):
        self.log.config(state="normal")
        self.log.insert("end", text, tag)
        self.log.config(state="disabled")
        self.log.see("end")

    def open(self, voice=False, prefill=""):
        if self.win is None:
            self._build()
        a = self.app
        rx, ry = a.root.winfo_x(), a.root.winfo_y()
        l, t, r, b = work_area(rx + a.W // 2, ry + a.pet_h // 2)
        x = rx - 430 if rx - 430 >= l else min(rx + a.W + 10, r - 420)
        y = max(t, min(ry - 300, b - 490))
        self.win.geometry(f"+{x}+{y}")
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self.entry.focus_set()
        if prefill:
            self.entry.delete(0, "end")
            self.entry.insert(0, prefill)
        if voice:
            self.win.after(450, self.start_voice)

    def close(self):
        self.voice = False
        self.win.withdraw()

    def reset(self):
        self.session_id = None
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self._write("New conversation started.\n", "dim")

    def start_voice(self):
        self.win.focus_force()
        self.entry.focus_set()
        self.entry.icursor("end")
        self.voice_start = self.entry.get()
        self.voice_last = self.voice_start
        self.voice_changed = time.time()
        self.voice = True
        self.status.config(text="Listening... speak now. I'll send it when you pause.", fg="#59e08a")
        self.win.after(150, press_win_h)

    def poll_voice(self):
        if not self.voice or self.win is None:
            return
        txt = self.entry.get()
        now = time.time()
        if txt != self.voice_last:
            self.voice_last, self.voice_changed = txt, now
        elif txt.strip() and txt != self.voice_start and now - self.voice_changed > 2.5:
            self.send()
        elif now - self.voice_changed > 30:
            self.voice = False
            self.status.config(text="Type, or press the mic and talk.", fg="#8a817b")

    def send(self):
        text = self.entry.get().strip()
        self.voice = False
        if not text or self.busy:
            return
        self.entry.delete(0, "end")
        self._write("\nYou: ", "you")
        self._write(text + "\n")
        self.busy = True
        self.status.config(text="Claude is on it...", fg="#ffb48a")
        sid = self.session_id
        q = self.app.events

        def work():
            q.put(("chat",) + self.app.claude.ask(text, session_id=sid))
        threading.Thread(target=work, daemon=True).start()

    def on_reply(self, text, sid, err):
        self.busy = False
        self.session_id = sid
        self.status.config(text="Type, or press the mic and talk.", fg="#8a817b")
        if err:
            self._write(f"\n{err}\n", "err")
        else:
            self._write("\nClaude: ", "claude")
            self._write(text + "\n")

# ---------------------------------------------------------------- the pet

LOCAL_TIPS = [
    "Tip: press Shift+Tab in Claude Code to cycle permission modes, including plan mode.",
    "Tip: start a message with # in Claude Code to save it to memory.",
    "Tip: /compact keeps a long Claude Code session snappy.",
    "Tip: you can drag a screenshot straight into Claude Code.",
    "Tip: ask Claude to write tests first, then make them pass.",
    "Tip: Esc twice in Claude Code lets you rewind to an earlier message.",
    "Tip: a CLAUDE.md in your repo teaches Claude your project's conventions.",
    "Stretch break! Even crabs need to wiggle their legs.",
    "Drink some water - stay hydrated like a crab.",
    "Stuck? Hover me and hit the mic - just say what's wrong.",
]
HELP_RE = re.compile(r"\b(error|exception|traceback|failed|failure|not working|stack ?overflow|"
                     r"how (do|to)|crash(ed)?|cannot|can't|bug|issue|problem|undefined|"
                     r"404|500|denied|refused)\b", re.I)
BEHAVIOURS = [("idle", 35), ("walk", 25), ("look", 10), ("wave", 8), ("dance", 6), ("bubble", 8), ("snap", 7)]


class CrabApp:
    TICK_MS = 120

    def __init__(self):
        self.cfg = load_config()
        root = self.root = tk.Tk()
        root.title(self.cfg["name"])
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.config(bg=KEY)
        root.attributes("-transparentcolor", KEY)

        dpi = root.winfo_fpixels("1i")
        self.S = int(self.cfg.get("scale") or max(3, round(4 * dpi / 96)))
        S = self.S
        self.W = 32 * S
        self.pet_h = 32 * S
        self.menu_h = 11 * S
        self.H = self.pet_h + self.menu_h
        self.cv = tk.Canvas(root, width=self.W, height=self.H, bg=KEY, highlightthickness=0)
        self.cv.pack()

        pos = self.cfg.get("position")
        if pos:
            x, y = pos
        else:
            l, t, r, b = work_area(0, 0)
            x, y = r - self.W - 120, b - self.H
        root.geometry(f"{self.W}x{self.H}+{int(x)}+{int(y)}")

        self.events = queue.Queue()
        self.claude = Claude(self.cfg)
        self.watcher = ActivityWatcher(self.cfg.get("wsl_distro") or "Ubuntu")
        self.watcher.start()
        self.bubble = Bubble(self)
        self.chat = ChatWindow(self)

        self.t = 0
        self.pose = "idle"
        self.behaviour = "idle"
        self.behaviour_until = 0
        self.facing = 1
        self.look = 0
        self.next_look = 0
        self.next_blink = time.time() + 3
        self.blink_until = 0
        self.react_pose, self.react_until = None, 0
        self.force_sleep = False
        self.dragging = False
        self.press = None
        self.hover_until = 0
        self.hover_btn = None
        self.menu_drawn = None
        self.last_px = None
        self.prev_activity = "idle"
        self.activity_since = time.time()

        # help/tips state
        self.last_tip = time.time() - 240
        self.next_random_tip = time.time() + random.randint(20, 40) * 60
        self.tipped_titles = {}
        self.switches = []
        self.last_hwnd = None
        self.active_since = time.time()
        self.stretch_nagged = False
        self.tip_pending = False

        # meeting notes state
        self.recorder = None
        self.rec_app = None
        self.rec_folder = None
        self.rec_auto = False
        self.rec_started_at = None
        self.rec_last_seen = 0
        self.meeting_offered = set()
        self.meeting_jobs = []
        self.meeting_deps = None

        # Claude Code permission requests
        self.bridge = claude_link.Bridge()
        self.perm_queue = []
        self.perm_current = None
        self.needs_until = 0

        cv = self.cv
        cv.bind("<ButtonPress-1>", self.on_press)
        cv.bind("<B1-Motion>", self.on_motion)
        cv.bind("<ButtonRelease-1>", self.on_release)
        cv.bind("<Button-3>", self.on_menu)

        self.ctx = tk.Menu(root, tearoff=0)
        self.walk_var = tk.BooleanVar(value=self.cfg.get("walk", True))
        self.tips_var = tk.BooleanVar(value=self.cfg.get("ai_tips", True))
        self.meet_var = tk.BooleanVar(value=self.cfg.get("meeting_notes", True))
        self.approve_var = tk.StringVar(value=self.cfg.get("approvals", "bubble"))
        self.sound_var = tk.BooleanVar(value=self.cfg.get("sounds", True))

        if not self.cfg.get("hooks_offered") and not claude_link.hooks_installed(self.cfg.get("wsl_distro")):
            self.say(f"Hi, I'm {self.cfg['name']}! Want me to show when Claude Code needs your permission, "
                     "so you can Allow or Deny right from here?", 120, actions=[
                         ("Connect me", lambda: self.connect_hooks(True)),
                         ("No thanks", lambda: self.connect_hooks(False))])
        else:
            self.say(f"Hi, I'm {self.cfg['name']}! Hover me for options, drag me anywhere.", 8)
        self.tick()
        self.slow_tick()

    # ---------------- helpers
    def _toggle_walk(self):
        self.cfg["walk"] = self.walk_var.get()
        save_config(self.cfg)

    def _toggle_tips(self):
        self.cfg["ai_tips"] = self.tips_var.get()
        save_config(self.cfg)

    def _toggle_meet(self):
        self.cfg["meeting_notes"] = self.meet_var.get()
        save_config(self.cfg)

    def _nap(self):
        self.force_sleep = True

    def build_menu(self):
        m = self.ctx
        m.delete(0, "end")
        m.add_command(label="Chat (voice)", command=lambda: self.chat.open(voice=True))
        m.add_command(label="Chat (type)", command=lambda: self.chat.open())
        m.add_command(label="Open Claude Code CLI", command=self.open_cli)
        m.add_separator()
        if self.recorder:
            m.add_command(label="Stop recording and write notes", command=self.stop_recording)
        else:
            m.add_command(label="Record a meeting now", command=lambda: self.start_recording("Meeting", auto=False))
        m.add_command(label="Open meeting notes", command=self.open_meetings_folder)
        m.add_checkbutton(label="Offer to take meeting notes", variable=self.meet_var, command=self._toggle_meet)
        m.add_separator()
        perms = tk.Menu(m, tearoff=0)
        for value, label in (("bubble", "Ask me with Allow / Deny"), ("notify", "Just let me know"),
                             ("off", "Ignore them")):
            perms.add_radiobutton(label=label, value=value, variable=self.approve_var, command=self._set_approvals)
        perms.add_separator()
        if claude_link.hooks_installed(self.cfg.get("wsl_distro")):
            perms.add_command(label="Disconnect from Claude Code", command=self.disconnect_hooks)
        else:
            perms.add_command(label="Connect to Claude Code", command=lambda: self.connect_hooks(True))
        m.add_cascade(label="Permission requests", menu=perms)
        m.add_checkbutton(label="Sounds", variable=self.sound_var, command=self._toggle_sound)
        m.add_separator()
        m.add_checkbutton(label="Wander around", variable=self.walk_var, command=self._toggle_walk)
        m.add_checkbutton(label="Smart tips (asks Claude)", variable=self.tips_var, command=self._toggle_tips)
        m.add_command(label="Take a nap", command=self._nap)
        m.add_separator()
        m.add_command(label=f"About {self.cfg['name']} {__version__}",
                      command=lambda: os.startfile(f"https://github.com/{REPO}"))
        m.add_command(label="Goodbye", command=self.quit)

    def _set_approvals(self):
        self.cfg["approvals"] = self.approve_var.get()
        save_config(self.cfg)

    def _toggle_sound(self):
        self.cfg["sounds"] = self.sound_var.get()
        save_config(self.cfg)

    def chime(self):
        if self.cfg.get("sounds", True):
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass

    # ---------------- Claude Code permission requests
    def connect_hooks(self, yes):
        self.cfg["hooks_offered"] = True
        save_config(self.cfg)
        if not yes:
            self.say("No problem! You can connect me later from my right-click menu.", 6)
            return
        results = claude_link.install_hooks(self.cfg.get("approval_wait_sec", 60), self.cfg.get("wsl_distro"))
        bad = [f"{p}: {e}" for p, e in results if e]
        if bad:
            self.say("I couldn't update some Claude Code settings:\n" + "\n".join(bad), 20)
        else:
            self.react("happy", 2)
            self.say("Connected! New Claude Code sessions will ask me when they need your permission.", 8)

    def disconnect_hooks(self):
        claude_link.uninstall_hooks(self.cfg.get("wsl_distro"))
        self.say("Disconnected. Claude Code will ask in its own window again.", 6)

    def poll_claude_requests(self):
        mode = self.cfg.get("approvals", "bubble")
        wait = int(self.cfg.get("approval_wait_sec", 60))
        for req in self.bridge.poll(mode, wait):
            event = req.get("hook_event_name")
            project = claude_link.project_name(req)
            if event == "PermissionRequest":
                if mode == "bubble":
                    self.perm_queue.append(req)
                elif mode == "notify":
                    verb, _ = claude_link.describe(req)
                    self.needs_you(f"{project}: Claude wants to {verb} and needs your OK.")
            elif event == "Notification" and mode != "off":
                kind = req.get("notification_type")
                if kind == "permission_prompt" and not self.perm_current and mode == "bubble":
                    self.needs_you(f"{project}: Claude is waiting for your permission in its window.")
                elif kind == "idle_prompt":
                    self.needs_you(f"{project}: Claude is done and waiting for you.", chime=False)
                elif kind in ("elicitation_dialog", "agent_needs_input"):
                    self.needs_you(f"{project}: Claude has a question for you.")

        if self.perm_current and not self.bridge.waiting(self.perm_current["_id"]):
            self.perm_current = None          # answered in the terminal, or timed out
            self.bubble.hide()
        if not self.perm_current and self.perm_queue:
            self.show_permission(self.perm_queue.pop(0))

    def needs_you(self, text, chime=True):
        self.needs_until = time.time() + 6
        self.force_sleep = False
        if chime:
            self.chime()
        self.say(text, 20)

    def show_permission(self, req):
        self.perm_current = req
        self.force_sleep = False
        verb, detail = claude_link.describe(req)
        more = f"  (+{len(self.perm_queue)} more)" if self.perm_queue else ""
        text = f"{claude_link.project_name(req)}: Claude wants to {verb}{more}"
        if detail:
            text += f"\n\n{detail}"
        rid = req["_id"]
        actions = [("Allow", lambda: self.answer(rid, {"behavior": "allow"}))]
        suggestions = req.get("permission_suggestions")
        if suggestions:
            actions.append(("Always allow", lambda: self.answer(
                rid, {"behavior": "allow", "updatedPermissions": suggestions})))
        actions += [("Deny", lambda: self.answer(rid, {"behavior": "deny", "message": "The user denied this from Clawde."})),
                    ("In terminal", lambda: self.answer(rid, None))]
        self.chime()
        self.bubble.show(text, int(self.cfg.get("approval_wait_sec", 60)), actions=actions)

    def answer(self, rid, decision):
        self.bridge.respond(rid, decision or {"behavior": "defer"})
        self.perm_current = None
        if decision and decision["behavior"] == "allow":
            self.react("happy", 1.2)

    def quit(self):
        if self.recorder:
            self.stop_recording()
        self.cfg["position"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_config(self.cfg)
        self.root.destroy()

    # ---------------- meetings
    def meetings_base(self):
        return Path(self.cfg.get("meetings_folder") or Path.home() / "Documents" / "Clawde Meetings")

    def open_meetings_folder(self):
        base = self.meetings_base()
        base.mkdir(parents=True, exist_ok=True)
        os.startfile(base)

    def have_meeting_deps(self):
        if self.meeting_deps is None:
            self.meeting_deps = meetings.deps_available()
        if not self.meeting_deps:
            self.say("I can take meeting notes once my extras are installed:\n"
                     "py -m pip install -r requirements-meetings.txt", 20)
        return self.meeting_deps

    def start_recording(self, app, auto):
        if self.recorder or not self.have_meeting_deps():
            return
        folder = meetings.new_meeting_folder(self.meetings_base(), app)
        rec = meetings.Recorder(folder)
        try:
            rec.start()
        except Exception as e:
            rec.stop()
            self.say(f"I couldn't start recording: {e}", 15)
            return
        self.recorder, self.rec_app, self.rec_folder, self.rec_auto = rec, app, folder, auto
        self.rec_started_at = datetime.now()
        self.rec_last_seen = time.time()
        self._write_meeting_meta(0)
        self.force_sleep = False
        self.react("happy", 1.5)
        self.say("Recording! I'll write up notes when the call ends." if auto else
                 "Recording! Right-click me and choose Stop when you're done.", 6)

    def _write_meeting_meta(self, seconds):
        meta = {"app": self.rec_app, "started": self.rec_started_at.isoformat(timespec="seconds"),
                "seconds": round(seconds)}
        (self.rec_folder / "meeting.json").write_text(json.dumps(meta), encoding="utf-8")

    def stop_recording(self, why=None):
        rec = self.recorder
        if not rec:
            return
        self.recorder = None
        self._write_meeting_meta(rec.stop())
        meetings.start_processing(self.rec_folder)
        self.meeting_jobs.append(self.rec_folder)
        self.say((why + "\n" if why else "") + "Writing up your notes now - this can take a few minutes.", 10)

    def meeting_tick(self):
        now = time.time()
        for folder in list(self.meeting_jobs):
            st = meetings.read_status(folder)
            if st["state"] == "done":
                self.meeting_jobs.remove(folder)
                self.react("happy", 3)
                self.say("Your meeting notes are ready! \U0001F4DD", 300, actions=[
                    ("Open notes", lambda n=st.get("notes"): open_file(n)),
                    ("Open folder", lambda f=folder: os.startfile(f))])
            elif st["state"] == "error":
                self.meeting_jobs.remove(folder)
                self.say(f"I couldn't finish those meeting notes ({st.get('error')}).", 60,
                         actions=[("Open folder", lambda f=folder: os.startfile(f))])

        if self.recorder:
            apps = meetings.meeting_apps_in_use()
            if self.rec_app in apps or (apps and not self.rec_auto):
                self.rec_last_seen = now
            if self.rec_auto and now - self.rec_last_seen > 20:
                self.stop_recording("The call ended, so I stopped recording.")
            elif self.recorder.elapsed() > 4 * 3600:
                self.stop_recording("That's 4 hours, so I stopped recording.")
            return
        if not self.cfg.get("meeting_notes", True):
            return
        apps = meetings.meeting_apps_in_use()
        if not apps:
            self.meeting_offered.clear()
            return
        new = [a for a in apps if a not in self.meeting_offered]
        if not new or self.dragging:
            return
        self.meeting_offered.update(apps)
        if not self.have_meeting_deps():
            return
        app = new[0]
        self.force_sleep = False
        self.react("alert", 4)
        self.say(f"Looks like you're on a {app} call. Want me to record it and write up notes?\n"
                 "Remember to let everyone know it's being recorded.", 90,
                 actions=[("Record & take notes", lambda: self.start_recording(app, auto=True)),
                          ("Not this time", lambda: None)])

    def say(self, text, secs=12, on_click=None, actions=None, urgent=False):
        if self.perm_current and not urgent:
            return                      # never cover a pending Allow/Deny
        self.bubble.show(text, secs, on_click, actions)

    def react(self, pose, secs):
        self.react_pose, self.react_until = pose, time.time() + secs

    def open_cli(self):
        def go():
            ok = self.claude.open_terminal()
            if not ok:
                self.events.put(("say", "I couldn't find Claude Code. Set claude_mode in crab_config.json."))
        threading.Thread(target=go, daemon=True).start()
        self.react("happy", 1.5)

    # ---------------- input
    def in_menu(self, y):
        return y >= self.pet_h

    def on_press(self, e):
        self.press = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y(), self.in_menu(e.y))
        self.dragging = False

    def on_motion(self, e):
        if not self.press or self.press[4]:
            return
        x0, y0, wx, wy, _ = self.press
        dx, dy = e.x_root - x0, e.y_root - y0
        if not self.dragging and abs(dx) + abs(dy) > 4:
            self.dragging = True
            self.bubble.hide()
        if self.dragging:
            self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def on_release(self, e):
        was_menu = self.press and self.press[4]
        self.press = None
        if self.dragging:
            self.dragging = False
            self.react("happy", 1.0)
            self.cfg["position"] = [self.root.winfo_x(), self.root.winfo_y()]
            save_config(self.cfg)
            return
        if was_menu:
            if e.x < self.W // 2:
                self.open_cli()
            else:
                self.chat.open(voice=True)
            return
        if self.recorder:
            mins = int(self.recorder.elapsed() // 60)
            what = "meeting" if self.rec_app == "Meeting" else f"{self.rec_app} call"
            self.say(f"Recording your {what} ({mins} min so far).", 20,
                     actions=[("Stop & write notes", self.stop_recording), ("Keep going", lambda: None)])
            return
        self.force_sleep = False
        self.react("happy", 2.0)
        if random.random() < 0.3:
            self.say(random.choice(["Hehe, that tickles!", "*snip snip*", "Hi friend!", "Need anything? Hover me!"]), 4)

    def on_menu(self, e):
        self.build_menu()
        self.ctx.tk_popup(e.x_root, e.y_root)

    # ---------------- behaviour
    def decide(self):
        now = time.time()
        if self.dragging:
            return "drag"
        if self.perm_current or now < self.needs_until:
            return "needs"
        if now < self.react_until:
            return self.react_pose
        act = self.watcher.status
        if self.chat.busy and act == "idle":
            act = "thinking"
        if act != self.prev_activity:
            if self.prev_activity != "idle" and act == "idle" and now - self.activity_since > 15:
                self.react("happy", 2.5)
                if random.random() < 0.4:
                    self.say("Claude's all done! \u2728", 5)
            self.prev_activity, self.activity_since = act, now
        if act == "working":
            return "work"
        if act == "thinking":
            return "think"
        if self.force_sleep or (idle_seconds() > self.cfg.get("sleep_after_sec", 180) and not self.recorder):
            return "sleep"
        if now >= self.behaviour_until:
            names = [b for b, _ in BEHAVIOURS if b != "walk" or self.cfg.get("walk", True)]
            weights = [w for b, w in BEHAVIOURS if b in names]
            self.behaviour = random.choices(names, weights)[0]
            span = {"idle": (3, 7), "walk": (2, 6), "look": (2, 4)}.get(self.behaviour, (1.5, 3))
            self.behaviour_until = now + random.uniform(*span)
            if self.behaviour == "walk":
                self.facing = random.choice((-1, 1))
        return self.behaviour

    def walk_step(self):
        x, y = self.root.winfo_x(), self.root.winfo_y()
        l, t, r, b = work_area(x + self.W // 2, y + self.pet_h // 2)
        nx = x + self.facing * max(2, self.S // 2 + 1)
        if nx < l or nx > r - self.W:
            self.facing *= -1
            nx = max(l, min(nx, r - self.W))
        self.root.geometry(f"+{nx}+{y}")

    # ---------------- drawing
    def draw_sprite(self, px):
        if px == self.last_px:
            return
        self.last_px = px
        cv, S = self.cv, self.S
        cv.delete("sprite")
        rows = {}
        for (x, y), c in px.items():
            rows.setdefault(y, []).append((x, c))
        for y, cells in rows.items():
            cells.sort()
            start, prev, col = cells[0][0], cells[0][0], cells[0][1]
            for x, c in cells[1:] + [(None, None)]:
                if x is not None and x == prev + 1 and c == col:
                    prev = x
                    continue
                cv.create_rectangle(start * S, y * S, (prev + 1) * S, (y + 1) * S, fill=col, outline="", tags="sprite")
                if x is not None:
                    start, prev, col = x, x, c
        cv.tag_raise("menu")

    def draw_menu(self, visible, btn):
        state = (visible, btn)
        if state == self.menu_drawn:
            return
        self.menu_drawn = state
        cv, S = self.cv, self.S
        cv.delete("menu")
        if not visible:
            return
        pw, ph = 24 * S, 9 * S
        x0 = (self.W - pw) // 2
        y0 = self.pet_h + S
        r = ph // 2
        bg = "#262626"
        cv.create_oval(x0, y0, x0 + ph, y0 + ph, fill=bg, outline="", tags="menu")
        cv.create_oval(x0 + pw - ph, y0, x0 + pw, y0 + ph, fill=bg, outline="", tags="menu")
        cv.create_rectangle(x0 + r, y0, x0 + pw - r, y0 + ph, fill=bg, outline="", tags="menu")
        cy = y0 + ph // 2
        lx, rx = x0 + pw // 4 + S // 2, x0 + 3 * pw // 4 - S // 2
        hr = int(3.6 * S)
        for which, cx in (("left", lx), ("right", rx)):
            if btn == which:
                cv.create_oval(cx - hr, cy - hr, cx + hr, cy + hr, fill="#414141", outline="", tags="menu")
        # left: terminal prompt
        bw, bh = int(4.4 * S), int(3.6 * S)
        cv.create_rectangle(lx - bw // 2, cy - bh // 2, lx + bw // 2, cy + bh // 2,
                            outline="#f2f2f2", width=max(1, S // 2), tags="menu")
        u = max(1, S // 2)
        cv.create_line(lx - bw // 2 + 2 * u, cy - 2 * u, lx - bw // 2 + 4 * u, cy, lx - bw // 2 + 2 * u, cy + 2 * u,
                       fill="#f2f2f2", width=u, tags="menu")
        cv.create_line(lx, cy + 2 * u, lx + bw // 2 - 2 * u, cy + 2 * u, fill="#f2f2f2", width=u, tags="menu")
        # divider
        cv.create_line(x0 + pw // 2, y0 + 2 * S, x0 + pw // 2, y0 + ph - 2 * S, fill="#4a4a4a", tags="menu")
        # right: voice waveform
        heights = (0.35, 0.7, 1.0, 0.6, 0.35)
        gap = int(0.9 * S)
        for i, hgt in enumerate(heights):
            x = rx + (i - 2) * gap
            hh = int(hgt * 1.8 * S)
            cv.create_line(x, cy - hh, x, cy + hh, fill="#f2f2f2", width=max(2, S // 2),
                           capstyle="round", tags="menu")
        cv.tag_raise("menu")

    # ---------------- loops
    def tick(self):
        try:
            self._tick()
        except Exception:
            pass
        self.root.after(self.TICK_MS, self.tick)

    def _tick(self):
        self.t += 1
        now = time.time()
        while not self.events.empty():
            self.handle_event(self.events.get())
        if self.t % 2 == 0:
            self.poll_claude_requests()

        pose = self.decide()
        if pose == "sleep" and self.pose != "sleep":
            self.bubble.hide()
        if self.pose == "sleep" and pose not in ("sleep", "drag"):
            if not self.force_sleep:
                self.react("happy", 1.2)
                pose = "happy"
        self.pose = pose

        if pose == "walk":
            self.walk_step()
        if pose == "look" and now >= self.next_look:
            self.look = random.choice((-1, 0, 1))
            self.next_look = now + random.uniform(0.6, 1.5)
        if now >= self.next_blink:
            self.blink_until = now + 0.2
            self.next_blink = now + random.uniform(2, 6)
        blink = now < self.blink_until
        look = self.look if pose == "look" else 0
        params = pose_params(pose, self.t, self.facing, look, blink)
        params["rec"] = self.recorder is not None
        self.draw_sprite(render(params))

        px, py = self.root.winfo_pointerxy()
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        if rx + 3 * self.S <= px < rx + self.W - 3 * self.S and ry + 6 * self.S <= py < ry + self.H:
            self.hover_until = now + 0.6
        visible = now < self.hover_until and not self.dragging
        btn = None
        if visible and py >= ry + self.pet_h:
            btn = "left" if px < rx + self.W // 2 else "right"
        self.draw_menu(visible, btn)

        self.bubble.follow()
        self.chat.poll_voice()

    def slow_tick(self):
        try:
            self._slow_tick()
        except Exception:
            pass
        self.root.after(2000, self.slow_tick)

    def _slow_tick(self):
        now = time.time()
        self.root.attributes("-topmost", True)
        self.root.lift()
        if self.bubble.visible:
            self.bubble.win.lift()
        if self.cfg.get("check_updates", True) and now - self.cfg.get("last_update_check", 0) > 86400:
            self.cfg["last_update_check"] = now
            save_config(self.cfg)
            threading.Thread(target=self._check_update, daemon=True).start()
        try:
            self.meeting_tick()
        except Exception as e:
            if not getattr(self, "meeting_error_shown", False):
                self.meeting_error_shown = True
                self.say(f"Meeting notes hit a snag: {e}", 15)

        idle = idle_seconds()
        if idle > 300:
            self.active_since = now
            self.stretch_nagged = False
        if self.pose in ("sleep", "drag") or self.bubble.visible or self.recorder:
            return

        hwnd, title = foreground_window()
        mine = title.startswith(self.cfg["name"]) or not title
        if hwnd != self.last_hwnd and not mine:
            self.switches.append(now)
            self.last_hwnd = hwnd
        self.switches = [s for s in self.switches if now - s < 60]

        if now - self.last_tip < 300:
            return

        if not mine and HELP_RE.search(title) and now - self.tipped_titles.get(title, 0) > 1800 and idle < 60:
            self.tipped_titles[title] = now
            self.last_tip = now
            ctx = f'I\'m looking at "{title}" and could use a hand.'
            if self.cfg.get("ai_tips", True) and not self.tip_pending:
                self.tip_pending = True
                self.react("alert", 2)
                threading.Thread(target=self._ai_tip, args=(title, ctx), daemon=True).start()
            else:
                self.react("alert", 3)
                self.say("Looks like something's giving you trouble. Want me to ask Claude?", 15,
                         lambda: self.chat.open(voice=False, prefill=ctx))
            return
        if len(self.switches) >= 15:
            self.switches.clear()
            self.last_tip = now
            self.react("alert", 2)
            self.say("Hunting for something? Tell me what you need and I'll ask Claude.", 12,
                     lambda: self.chat.open(voice=True))
            return
        if now - self.active_since > 55 * 60 and not self.stretch_nagged:
            self.stretch_nagged = True
            self.last_tip = now
            self.react("wave", 3)
            self.say("You've been at it for an hour - stretch with me! \U0001F980", 10)
            return
        if now >= self.next_random_tip and idle < 30:
            self.next_random_tip = now + random.randint(25, 45) * 60
            self.last_tip = now
            self.react("wave", 2)
            self.say(random.choice(LOCAL_TIPS), 12)

    def _ai_tip(self, title, ctx):
        prompt = ("You are Clawde, a tiny friendly crab that lives on the user's desktop. "
                  f"The user's active window is titled: \"{title}\". It looks like they might need help. "
                  "Give ONE short, concrete, friendly suggestion (max 30 words) that could help with that. "
                  "No preamble, no markdown, do not use tools.")
        text, _, err = self.claude.ask(prompt, model="haiku", timeout=90)
        self.events.put(("tip", text if text and not err else None, ctx))

    def _check_update(self):
        import urllib.request
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers={"User-Agent": "clawde"})
            with urllib.request.urlopen(req, timeout=15) as r:
                rel = json.loads(r.read().decode("utf-8"))
        except Exception:
            return

        def ver(s):
            return tuple(int(x) for x in re.findall(r"\d+", s)[:3])
        tag = rel.get("tag_name") or ""
        if tag and ver(tag) > ver(__version__):
            self.events.put(("update", tag, rel.get("html_url") or f"https://github.com/{REPO}/releases"))

    def handle_event(self, ev):
        kind = ev[0]
        if kind == "update":
            self.say(f"A new version of me is out ({ev[1]})!", 60,
                     actions=[("Download", lambda: os.startfile(ev[2])), ("Later", lambda: None)])
            return
        if kind == "chat":
            self.chat.on_reply(*ev[1:])
            self.react("happy", 1.5)
        elif kind == "tip":
            self.tip_pending = False
            text, ctx = ev[1], ev[2]
            msg = (text[:220] + "\n\nClick to chat about it.") if text else \
                "Looks like something's giving you trouble. Want me to ask Claude?"
            self.react("alert", 3)
            self.say(msg, 20, lambda: self.chat.open(voice=False, prefill=ctx))
        elif kind == "say":
            self.say(ev[1], 8)

    def run(self):
        self.root.mainloop()

# ---------------------------------------------------------------- preview

def preview(path):
    root = tk.Tk()
    root.withdraw()
    poses = ["idle", "walk", "look", "wave", "dance", "bubble", "snap", "sleep", "think", "work", "drag", "happy",
             "alert", "idle+rec", "work+rec"]
    S, cols = 6, 5
    rows = (len(poses) + cols - 1) // cols
    img = tk.PhotoImage(width=cols * 32 * S, height=rows * 32 * S)
    img.put("#d8dde6", to=(0, 0, cols * 32 * S, rows * 32 * S))
    for n, name in enumerate(poses):
        ox, oy = (n % cols) * 32 * S, (n // cols) * 32 * S
        img.put("#eef1f5" if (n % 2) else "#e4e8ee", to=(ox, oy, ox + 32 * S, oy + 32 * S))
        p = pose_params(name.split("+")[0], 4, 1, 1)
        p["rec"] = name.endswith("+rec")
        for (x, y), c in render(p).items():
            img.put(c, to=(ox + x * S, oy + y * S, ox + (x + 1) * S, oy + (y + 1) * S))
    img.write(path, format="png")
    root.destroy()


def main():
    if "--preview" in sys.argv:
        preview(sys.argv[sys.argv.index("--preview") + 1])
        return
    if "--process-meeting" in sys.argv:
        meetings.process(sys.argv[sys.argv.index("--process-meeting") + 1])
        return
    if "--install-hooks" in sys.argv:
        cfg = load_config()
        claude_link.install_hooks(cfg.get("approval_wait_sec", 60), cfg.get("wsl_distro"))
        cfg["hooks_offered"] = True
        save_config(cfg)
        return
    if "--uninstall-hooks" in sys.argv:
        claude_link.uninstall_hooks(load_config().get("wsl_distro"))
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    CrabApp().run()


if __name__ == "__main__":
    main()
