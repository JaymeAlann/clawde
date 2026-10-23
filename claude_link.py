"""Connects Clawde to Claude Code: adds/removes the hook in settings.json and
answers the requests the hook drops in the bridge folder (see clawde_hook.py)."""
import ctypes
import json
import os
import sys
import time
from pathlib import Path


def user_profile():
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, buf)  # CSIDL_PROFILE
    return Path(buf.value)


# ~/.clawde rather than AppData: packaged apps (like the Claude desktop app, which runs
# Claude Code there) get their AppData writes redirected to a private copy.
DATA = user_profile() / ".clawde"
BRIDGE = DATA / "bridge"
HOOK_EVENTS = ("PermissionRequest", "Notification")
FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent

# ---------------------------------------------------------------- settings.json

def to_wsl(path):
    p = Path(path)
    return f"/mnt/{p.drive[0].lower()}{p.as_posix()[2:]}"


def hook_handler(target, wait):
    if FROZEN:
        exe = APP_DIR / "hook" / "clawde-hook.exe"
        cmd, args = (str(exe), []) if target == "windows" else (to_wsl(exe), [])
        args += ["--bridge", str(BRIDGE)]          # the .exe always runs on Windows, even from WSL
    elif target == "windows":
        cmd = str(Path(sys.executable).with_name("python.exe"))
        args = [str(APP_DIR / "clawde_hook.py"), "--bridge", str(BRIDGE)]
    else:
        cmd, args = "python3", [to_wsl(APP_DIR / "clawde_hook.py"), "--bridge", to_wsl(BRIDGE)]
    return {"type": "command", "command": cmd, "args": args, "timeout": int(wait) + 30}


def settings_files(wsl_distro="Ubuntu"):
    """(target, path) for every Claude Code install we can see."""
    found = [("windows", Path.home() / ".claude" / "settings.json")]
    for base in (rf"\\wsl.localhost\{wsl_distro}\home", rf"\\wsl$\{wsl_distro}\home"):
        try:
            homes = [Path(e.path) for e in os.scandir(base) if e.is_dir()]
        except OSError:
            continue
        found += [("wsl", h / ".claude" / "settings.json") for h in homes if (h / ".claude").is_dir()]
        break
    return found


def _is_ours(handler):
    text = " ".join([str(handler.get("command", ""))] + [str(a) for a in handler.get("args", [])]).lower()
    return "clawde" in text


def _strip(settings):
    hooks = settings.get("hooks") or {}
    for event in list(hooks):
        groups = []
        for g in hooks[event]:
            kept = [h for h in g.get("hooks", []) if not _is_ours(h)]
            if kept:
                groups.append({**g, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if "hooks" in settings and not hooks:
        del settings["hooks"]


def _load(path):
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def install_hooks(wait=60, wsl_distro="Ubuntu"):
    """Add Clawde's hook to every settings.json. Returns a list of (path, error-or-None)."""
    results = []
    for target, path in settings_files(wsl_distro):
        try:
            settings = _load(path)
            backup = path.with_name("settings.json.clawde-backup")
            if path.exists() and not backup.exists():
                backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            _strip(settings)
            hooks = settings.setdefault("hooks", {})
            for event in HOOK_EVENTS:
                hooks.setdefault(event, []).append({"hooks": [hook_handler(target, wait)]})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            results.append((path, None))
        except (OSError, ValueError) as e:
            results.append((path, str(e)))
    return results


def uninstall_hooks(wsl_distro="Ubuntu"):
    results = []
    for _, path in settings_files(wsl_distro):
        try:
            if not path.exists():
                continue
            settings = _load(path)
            before = json.dumps(settings)
            _strip(settings)
            if json.dumps(settings) != before:
                path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            results.append((path, None))
        except (OSError, ValueError) as e:
            results.append((path, str(e)))
    return results


def hooks_installed(wsl_distro="Ubuntu"):
    for _, path in settings_files(wsl_distro):
        try:
            for groups in (_load(path).get("hooks") or {}).values():
                if any(_is_ours(h) for g in groups for h in g.get("hooks", [])):
                    return True
        except (OSError, ValueError):
            continue
    return False

# ---------------------------------------------------------------- bridge

class Bridge:
    def __init__(self):
        self.dirs = {n: BRIDGE / n for n in ("requests", "acks", "responses")}
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)
            for f in d.iterdir():           # leftovers from a previous run
                try:
                    f.unlink()
                except OSError:
                    pass
        self.seen = set()

    def poll(self, mode, wait):
        """New hook requests, oldest first. Permission requests get an ack right away."""
        out = []
        try:
            files = sorted(self.dirs["requests"].glob("*.json"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return out
        for f in files:
            rid = f.stem
            if rid in self.seen:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue                    # still being written
            self.seen.add(rid)
            data["_id"] = rid
            if data.get("hook_event_name") == "PermissionRequest":
                self._write("acks", rid, {"mode": mode, "wait": wait})
            else:
                try:
                    f.unlink()
                except OSError:
                    pass
            out.append(data)
        return out

    def waiting(self, rid):
        return (self.dirs["requests"] / f"{rid}.json").exists()

    def respond(self, rid, decision):
        self._write("responses", rid, decision)

    def _write(self, kind, rid, data):
        path = self.dirs[kind] / f"{rid}.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

# ---------------------------------------------------------------- display

def project_name(req):
    cwd = str(req.get("cwd") or "").replace("\\", "/").rstrip("/")
    return cwd.rsplit("/", 1)[-1] or "Claude Code"


def describe(req):
    """(what Claude wants to do, the detail to show) for a permission request."""
    tool = req.get("tool_name") or "a tool"
    ti = req.get("tool_input") or {}
    if tool in ("Bash", "PowerShell"):
        verb, detail = "run a command", ti.get("command")
    elif tool == "Write":
        verb, detail = "create a file", ti.get("file_path")
    elif tool in ("Edit", "MultiEdit", "NotebookEdit"):
        verb, detail = "edit a file", ti.get("file_path") or ti.get("notebook_path")
    elif tool == "Read":
        verb, detail = "read a file", ti.get("file_path")
    elif tool == "WebFetch":
        verb, detail = "open a web page", ti.get("url")
    elif tool == "WebSearch":
        verb, detail = "search the web", ti.get("query")
    elif tool == "ExitPlanMode":
        verb, detail = "start working on its plan", (ti.get("plan") or "").strip().splitlines()[0:1]
        detail = detail[0] if detail else ""
    elif tool.startswith("mcp__"):
        parts = tool.split("__")
        verb = f"use {parts[1]}" + (f" ({parts[2]})" if len(parts) > 2 else "")
        detail = next((v for v in ti.values() if isinstance(v, str)), "")
    else:
        verb, detail = f"use {tool}", json.dumps(ti)
    detail = str(detail or "").strip()
    if len(detail) > 180:
        detail = detail[:177] + "..."
    return verb, detail
