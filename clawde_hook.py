"""Claude Code hook that hands permission requests and notifications to Clawde.

Claude Code runs this for PermissionRequest and Notification events (Clawde
adds it to ~/.claude/settings.json). It talks to Clawde through files in
%USERPROFILE%\\.clawde\\bridge, which works for Windows and WSL alike:

  requests/<id>.json   written here     the hook's input
  acks/<id>.json       written by Clawde "I'm here", with the approval mode
  responses/<id>.json  written by Clawde the decision

If Clawde isn't running, or you don't answer in time, the hook prints nothing
and Claude Code shows its normal permission prompt. Standard library only.
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

ACK_WAIT = 3.0


def bridge_dir():
    if "--bridge" in sys.argv:
        return Path(sys.argv[sys.argv.index("--bridge") + 1])
    if os.name == "nt":
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, buf)  # CSIDL_PROFILE
        return Path(buf.value) / ".clawde" / "bridge"
    for user in Path("/mnt/c/Users").glob("*"):  # WSL without --bridge
        p = user / ".clawde" / "bridge"
        if p.is_dir():
            return p
    return None


def write_atomic(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def wait_for(path, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        time.sleep(0.1)
    return None


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        return
    bridge = bridge_dir()
    if not bridge or not bridge.is_dir():
        return
    rid = uuid.uuid4().hex
    req = bridge / "requests" / f"{rid}.json"
    ack = bridge / "acks" / f"{rid}.json"
    resp = bridge / "responses" / f"{rid}.json"
    data["_wsl"] = os.name != "nt"
    try:
        req.parent.mkdir(exist_ok=True)
        write_atomic(req, data)
    except OSError:
        return
    if data.get("hook_event_name") != "PermissionRequest":
        return  # notifications are fire-and-forget

    try:
        info = wait_for(ack, ACK_WAIT)
        if not info or info.get("mode") != "bubble":
            return  # Clawde isn't running, or only shows that you're needed
        decision = wait_for(resp, float(info.get("wait", 60)))
        if decision and decision.get("behavior") in ("allow", "deny"):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}))
    finally:
        for p in (req, ack, resp):
            try:
                p.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
