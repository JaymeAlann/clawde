"""Clawde Wrapped: local stats about your time with Claude Code, and a shareable card.

Stats live in ~/.clawde/stats.json, one entry per day. Nothing leaves the PC
unless you copy or save the card and share it yourself. The card needs Pillow.
"""
import ctypes
import io
import json
import time
from datetime import date, timedelta
from pathlib import Path

KEEP_DAYS = 60


class Stats:
    def __init__(self, path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("days", {})
        self.dirty = False
        self.saved_at = time.time()

    def today(self):
        d = self.data["days"].setdefault(date.today().isoformat(), {})
        for k, v in (("sessions", []), ("agents", []), ("projects", {}), ("work_sec", 0), ("allowed", 0),
                     ("denied", 0), ("meetings", 0)):
            d.setdefault(k, v)
        return d

    # ---- recording
    def tick(self, sessions, agents, dt):
        """sessions: {transcript path: project} working right now; agents: live subagent dicts."""
        d = self.today()
        for path, project in sessions.items():
            sid = Path(path).stem[-12:]
            if sid not in d["sessions"]:
                d["sessions"].append(sid)
            d["projects"][project] = d["projects"].get(project, 0) + dt
        if sessions:
            d["work_sec"] += dt
        for a in agents:
            aid = Path(a["id"]).stem[-12:]
            if aid not in d["agents"]:
                d["agents"].append(aid)
        self.dirty = True
        if time.time() - self.saved_at > 30:
            self.save()

    def count(self, key):
        self.today()[key] += 1
        self.dirty = True
        self.save()

    def save(self):
        if not self.dirty:
            return
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        self.data["days"] = {k: v for k, v in self.data["days"].items() if k >= cutoff}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
            self.dirty, self.saved_at = False, time.time()
        except OSError:
            pass

    # ---- reading
    def summary(self, span):
        """span: "day" or "week" (the last 7 days)."""
        n = 1 if span == "day" else 7
        days = [(date.today() - timedelta(days=i)).isoformat() for i in range(n)]
        picked = [self.data["days"].get(k, {}) for k in days]
        projects = {}
        for d in picked:
            for p, s in d.get("projects", {}).items():
                projects[p] = projects.get(p, 0) + s
        streak = 0
        while self.data["days"].get((date.today() - timedelta(days=streak)).isoformat(), {}).get("work_sec"):
            streak += 1
        return {
            "span": span,
            "start": date.today() - timedelta(days=n - 1),
            "end": date.today(),
            "sessions": sum(len(d.get("sessions", [])) for d in picked),
            "work_sec": sum(d.get("work_sec", 0) for d in picked),
            "agents": sum(len(d.get("agents", [])) for d in picked),
            "allowed": sum(d.get("allowed", 0) for d in picked),
            "denied": sum(d.get("denied", 0) for d in picked),
            "meetings": sum(d.get("meetings", 0) for d in picked),
            "top_project": max(projects, key=projects.get) if projects else None,
            "active_days": sum(1 for d in picked if d.get("work_sec")),
            "streak": streak,
        }

# ---------------------------------------------------------------- the card

def _font(size, bold=False, black=False):
    from PIL import ImageFont
    names = (["seguibl.ttf"] if black else []) + (["segoeuib.ttf"] if bold or black else ["segoeui.ttf"]) + ["arial.ttf"]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _duration(sec):
    m = int(sec // 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def render_card(s, sprite, palettes, name="Clawde", url="github.com/JaymeAlann/clawde"):
    """Draw the 1200x675 card. sprite(pose, t, scale, palette=None, balls=None) -> RGBA image;
    palettes: three baby colour palettes (shell, highlight, shade)."""
    from PIL import Image, ImageDraw
    W, H = 1200, 675
    img = Image.new("RGBA", (W, H))
    top, bottom = (27, 31, 46), (46, 30, 44)
    d = ImageDraw.Draw(img)
    for y in range(H):
        f = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3)))

    # header
    d.text((60, 46), f"{name.upper()} WRAPPED", font=_font(22, bold=True), fill="#ff9a6b")
    title = "My day with Claude" if s["span"] == "day" else "My week with Claude"
    d.text((58, 76), title, font=_font(58, black=True), fill="white")
    when = s["end"].strftime("%A %d %B %Y") if s["span"] == "day" else \
        f"{s['start'].strftime('%d %b')} - {s['end'].strftime('%d %b %Y')}"
    d.text((60, 152), when, font=_font(24), fill="#a7a2b8")

    # tiles
    tiles = [
        (str(s["sessions"]), "Claude Code sessions"),
        (_duration(s["work_sec"]), "Claude spent working"),
        (str(s["agents"]), "baby crabs hatched"),
        (str(s["allowed"]), "permissions approved"),
        (str(s["meetings"]), "meetings summarised"),
        (s["top_project"] or "-", "top project"),
    ]
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    tw, th, gap, x0, y0 = 220, 170, 18, 60, 214
    for i, (value, label) in enumerate(tiles):
        x, y = x0 + (i % 3) * (tw + gap), y0 + (i // 3) * (th + gap)
        ld.rounded_rectangle([x, y, x + tw, y + th], radius=18, fill=(255, 255, 255, 18), outline=(255, 255, 255, 34))
    img.alpha_composite(layer)
    d = ImageDraw.Draw(img)
    for i, (value, label) in enumerate(tiles):
        x, y = x0 + (i % 3) * (tw + gap), y0 + (i // 3) * (th + gap)
        size = 60                               # shrink until it fits the tile, then trim
        while size > 22 and d.textlength(value, font=_font(size, black=True)) > tw - 40:
            size -= 2
        while len(value) > 4 and d.textlength(value, font=_font(size, black=True)) > tw - 40:
            value = value[:-4] + "..."
        d.text((x + 22, y + 22 + (60 - size) // 2), value, font=_font(size, black=True),
               fill="#ff8a50" if i % 2 == 0 else "white")
        d.text((x + 22, y + th - 46), label, font=_font(20), fill="#c9c3d6")

    # Clawde, juggling if he had help, with his babies
    babies = min(3, s["agents"])
    crab = sprite("juggle" if babies else "happy", 6, 10,
                  balls=[p[0] for p in palettes] if babies else None)
    img.alpha_composite(crab, (W - 340 - 40, 190))
    for i in range(babies):
        b = sprite("work", 3 + i * 4, 4, palette=palettes[i])
        img.alpha_composite(b, (W - 420 + i * 120, 470))

    # footer
    d = ImageDraw.Draw(img)
    footer = f"{s['streak']}-day streak" if s["streak"] > 1 else f"{s['active_days']} active day" + ("s" if s["active_days"] != 1 else "")
    d.text((60, H - 62), url, font=_font(22, bold=True), fill="#ff9a6b")
    fw = d.textlength(footer, font=_font(22, bold=True))
    d.text((W - 60 - fw, H - 62), footer, font=_font(22, bold=True), fill="#ffd84a")
    return img


def copy_to_clipboard(img):
    """Put the image on the Windows clipboard (CF_DIB), ready to paste into X, LinkedIn or Slack."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    data = buf.getvalue()[14:]                  # drop the BMP file header
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.OpenClipboard.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    h = k32.GlobalAlloc(0x0002, len(data))      # GMEM_MOVEABLE
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    if not u32.OpenClipboard(None):
        raise OSError("the clipboard is busy")
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(8, h):      # CF_DIB
            raise OSError("couldn't put the image on the clipboard")
    finally:
        u32.CloseClipboard()
