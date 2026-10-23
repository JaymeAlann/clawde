"""One-click updates from GitHub releases: find the newest installer, download
it, check it against the SHA-256 GitHub publishes for it, and run it silently."""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

UA = {"User-Agent": "clawde-updater"}
ASSET = re.compile(r"Clawde-Setup-[\d.]+\.exe")
DETACHED = 0x00000008 | 0x00000200        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def version_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3])


def latest_release(repo, timeout=15):
    """The newest release and its installer asset. CLAWDE_UPDATE_API overrides the URL (for testing)."""
    url = os.environ.get("CLAWDE_UPDATE_API") or f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rel = json.loads(r.read().decode("utf-8"))
    asset = next((a for a in rel.get("assets", []) if ASSET.fullmatch(a.get("name", ""))), None)
    return {"tag": rel.get("tag_name") or "", "page": rel.get("html_url") or f"https://github.com/{repo}/releases",
            "asset": asset}


def download(asset, progress=None):
    """Download the installer to %TEMP% and verify it. Returns its path; raises ValueError if it doesn't check out."""
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise ValueError("this release has no checksum to verify the download against")
    want = digest.split(":", 1)[1].lower()
    folder = Path(tempfile.gettempdir()) / "clawde-update"
    folder.mkdir(exist_ok=True)
    dest = folder / asset["name"]
    part = dest.with_suffix(".part")
    total, got, h = int(asset.get("size") or 0), 0, hashlib.sha256()
    req = urllib.request.Request(asset["browser_download_url"], headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            got += len(chunk)
            if progress and total:
                progress(got / total)
    if h.hexdigest() != want or (total and got != total):
        part.unlink(missing_ok=True)
        raise ValueError("the download didn't match its checksum, so I threw it away")
    os.replace(part, dest)
    return dest


def run_installer(path, relaunch=True):
    """Start the installer silently, with the options chosen at the first install.
    It closes the running Clawde, upgrades in place, and (with relaunch) starts the new one."""
    args = [str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
    if relaunch:
        args.append("/RELAUNCH=1")
    subprocess.Popen(args, creationflags=DETACHED, close_fds=True)
