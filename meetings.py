"""Meeting notes for Clawde.

Spots calls (an app like Teams or Zoom using the microphone), records your mic
and the call audio to separate tracks, transcribes them on this PC with
Whisper, and has Claude Code write notes with action items.

Needs the extras:  py -m pip install -r requirements-meetings.txt
Run a job by hand: py meetings.py process "<meeting folder>"
"""
import json
import re
import subprocess
import sys
import threading
import time
import wave
import winreg
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
NO_WINDOW = 0x08000000
OUT_RATE = 16000

MEETING_APPS = re.compile(r"teams|zoom|webex|slack|discord|skype|gotomeeting|ringcentral|whatsapp|signal|"
                          r"chrome|msedge|firefox|brave|opera|vivaldi|arc\b", re.I)
NOT_MEETINGS = re.compile(r"claude|soundrecorder|obs64|python|resolve", re.I)
FRIENDLY = {"msteams": "Teams", "ms-teams": "Teams", "teams": "Teams", "zoom": "Zoom", "chrome": "Chrome",
            "msedge": "Edge", "firefox": "Firefox", "slack": "Slack", "discord": "Discord", "webex": "Webex",
            "ciscocollabhost": "Webex", "brave": "Brave", "opera": "Opera"}


def deps_available():
    try:
        import numpy  # noqa: F401
        import pyaudiowpatch  # noqa: F401
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False

# ---------------------------------------------------------------- detection

def mic_users():
    """Names of the apps holding the microphone right now (from Windows' privacy tracking)."""
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    found = []

    def in_use(key):
        try:
            start = winreg.QueryValueEx(key, "LastUsedTimeStart")[0]
            stop = winreg.QueryValueEx(key, "LastUsedTimeStop")[0]
            return bool(start) and stop == 0
        except OSError:
            return False

    def subkeys(key):
        i = 0
        while True:
            try:
                yield winreg.EnumKey(key, i)
            except OSError:
                return
            i += 1

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
            for name in subkeys(root):
                with winreg.OpenKey(root, name) as k:
                    if name == "NonPackaged":
                        for exe in subkeys(k):
                            with winreg.OpenKey(k, exe) as e:
                                if in_use(e):
                                    found.append(exe.split("#")[-1].rsplit(".", 1)[0])
                    elif in_use(k):
                        found.append(name.split("_")[0])
    except OSError:
        pass
    return found


def friendly(name):
    return FRIENDLY.get(name.lower(), name)


def meeting_apps_in_use():
    return sorted({friendly(n) for n in mic_users() if MEETING_APPS.search(n) and not NOT_MEETINGS.search(n)})

# ---------------------------------------------------------------- recording

class _Track:
    """One mono 16 kHz WAV, padded with silence so it stays in step with the clock."""

    def __init__(self, path, t0):
        self.t0 = t0
        self.written = 0
        self.lock = threading.Lock()
        self.wav = wave.open(str(path), "wb")
        self.wav.setnchannels(1)
        self.wav.setsampwidth(2)
        self.wav.setframerate(OUT_RATE)

    def write(self, samples):
        import numpy as np
        with self.lock:
            if self.wav is None:
                return
            self._pad(np, slack=0.5)
            self.wav.writeframes(samples.astype(np.int16).tobytes())
            self.written += len(samples)

    def _pad(self, np, slack):
        behind = int((time.monotonic() - self.t0) * OUT_RATE) - self.written
        if behind > slack * OUT_RATE:
            fill = behind - (int(0.1 * OUT_RATE) if slack else 0)
            if fill > 0:
                self.wav.writeframes(np.zeros(fill, np.int16).tobytes())
                self.written += fill

    def pad(self, slack=0.5):
        import numpy as np
        with self.lock:
            if self.wav is not None:
                self._pad(np, slack)

    def close(self):
        with self.lock:
            if self.wav is not None:
                self.wav.close()
                self.wav = None


class Recorder:
    """Records the default mic to you.wav and what you hear to others.wav."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.started = None
        self._streams = []
        self._tracks = []
        self._pa = None
        self._ticker = None

    def start(self):
        import numpy as np
        import pyaudiowpatch as pa
        self.folder.mkdir(parents=True, exist_ok=True)
        p = self._pa = pa.PyAudio()
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        mic = p.get_device_info_by_index(wasapi["defaultInputDevice"])
        spk = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        loop = next((d for d in p.get_loopback_device_info_generator() if spk["name"] in d["name"]), None)
        self.started = time.monotonic()
        for dev, fname in ((mic, "you.wav"), (loop, "others.wav")):
            if dev is None:
                continue
            track = _Track(self.folder / fname, self.started)
            rate, ch = int(dev["defaultSampleRate"]), max(1, int(dev["maxInputChannels"]))

            def callback(data, frames, info, status, track=track, rate=rate, ch=ch):
                x = np.frombuffer(data, np.int16).reshape(-1, ch).mean(axis=1)
                if rate % OUT_RATE == 0:
                    f = rate // OUT_RATE
                    x = x[:len(x) // f * f].reshape(-1, f).mean(axis=1)
                elif rate != OUT_RATE:
                    idx = np.arange(0, len(x), rate / OUT_RATE)
                    x = np.interp(idx, np.arange(len(x)), x)
                track.write(x)
                return None, pa.paContinue

            stream = p.open(format=pa.paInt16, channels=ch, rate=rate, input=True,
                            input_device_index=dev["index"], frames_per_buffer=2048, stream_callback=callback)
            self._streams.append(stream)
            self._tracks.append(track)
        self._ticker = threading.Thread(target=self._tick, daemon=True)
        self._ticker.start()

    def _tick(self):
        # call audio delivers nothing while everyone is quiet; keep the track's clock moving
        while self._tracks and self._tracks[0].wav is not None:
            for t in self._tracks:
                t.pad()
            time.sleep(1)

    def stop(self):
        for s in self._streams:
            try:
                s.stop_stream()
                s.close()
            except Exception:
                pass
        for t in self._tracks:
            t.pad(slack=0)
            t.close()
        if self._pa:
            self._pa.terminate()
        self._streams, self._pa = [], None
        return time.monotonic() - (self.started or time.monotonic())

    def elapsed(self):
        return time.monotonic() - self.started if self.started else 0

# ---------------------------------------------------------------- processing

def stamp(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def transcribe(folder, model_name="small", language=None, progress=None):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs = []
    for label, fname in (("You", "you.wav"), ("Others", "others.wav")):
        path = folder / fname
        if not path.exists() or path.stat().st_size < 44 + OUT_RATE * 2:
            continue
        if progress:
            progress(f"transcribing {label.lower()}")
        # the tracks are already 16 kHz mono, so skip faster-whisper's PyAV decoder (it breaks on new PyAV versions)
        import numpy as np
        with wave.open(str(path)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        parts, _ = model.transcribe(audio, language=language or None, vad_filter=True, beam_size=1,
                                    condition_on_previous_text=False, word_timestamps=True)
        # Whisper's segments can span long pauses; regroup by word timing so turns interleave correctly
        for s in parts:
            cur = None
            for wd in s.words or []:
                if cur and wd.start - cur["end"] < 1.0:
                    cur["text"] += wd.word
                    cur["end"] = wd.end
                else:
                    if cur:
                        segs.append(cur)
                    cur = {"start": wd.start, "end": wd.end, "who": label, "text": wd.word}
            if cur:
                segs.append(cur)
    for s in segs:
        s["text"] = s["text"].strip()
    segs = [s for s in segs if s["text"]]

    # without headphones the mic hears the call too; drop "You" lines that echo "Others"
    others = [s for s in segs if s["who"] == "Others"]

    def echoed(s):
        return any(o["start"] < s["end"] + 1 and s["start"] < o["end"] + 1 and
                   SequenceMatcher(None, s["text"].lower(), o["text"].lower()).ratio() > 0.7 for o in others)
    segs = [s for s in segs if s["who"] == "Others" or not echoed(s)]
    segs.sort(key=lambda s: s["start"])

    merged = []
    for s in segs:
        if merged and merged[-1]["who"] == s["who"] and s["start"] - merged[-1]["end"] < 2:
            merged[-1]["text"] += " " + s["text"]
            merged[-1]["end"] = s["end"]
        else:
            merged.append(dict(s))
    return merged


SUMMARY_PROMPT = """You are writing meeting notes from an automatic transcript.
The meeting was recorded on {date} during a {app} call and lasted about {minutes} minutes.
"You" is the person who recorded it. "Others" is everyone else on the call (they are not told apart).
The transcript was made by speech recognition and may contain mistakes; use context to fix obvious ones.

Write the notes in Markdown with exactly these parts:
# <short descriptive title>
## Summary
3-6 bullets on what was discussed.
## Decisions
Bullets, or "None noted."
## Action items
A checklist, one per task: "- [ ] **Owner** - task (due date if one was mentioned)". Use real names when they
are said, "You" for the recorder, or "Unassigned". Write "None noted." if there are none.
## Open questions
Bullets, or leave this section out.
## Key details
Numbers, dates, names, links or other specifics worth keeping. Leave this section out if there are none.

Output only the notes. Do not use any tools.

Transcript:
{transcript}
"""


def process(folder):
    """Transcribe a recorded meeting folder and write notes.md. Progress goes to status.json."""
    folder = Path(folder)
    meta = json.loads((folder / "meeting.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(HERE))
    from crab_pet import Claude, load_config
    cfg = load_config()

    def status(state, **extra):
        (folder / "status.json").write_text(json.dumps({"state": state, **extra}), encoding="utf-8")

    try:
        status("transcribing")
        segs = transcribe(folder, cfg.get("whisper_model") or "small", cfg.get("meeting_language") or None,
                          progress=lambda m: status("transcribing", detail=m))
        lines = [f"**[{stamp(s['start'])}] {s['who']}:** {s['text']}" for s in segs]
        when = datetime.fromisoformat(meta["started"])
        header = f"# Transcript - {meta['app']} call, {when:%A %d %B %Y, %H:%M}\n\n"
        (folder / "transcript.md").write_text(header + ("\n\n".join(lines) or "_Nothing was heard._") + "\n",
                                              encoding="utf-8")
        minutes = max(1, round(meta.get("seconds", 0) / 60))
        footer = (f"\n\n---\n*Recorded by Clawde on {when:%d %B %Y at %H:%M} ({minutes} min, {meta['app']}). "
                  f"[Full transcript](transcript.md)*\n")
        if not segs:
            notes = "# Meeting notes\n\nI couldn't hear any speech in this recording."
        else:
            status("summarizing")
            prompt = SUMMARY_PROMPT.format(date=f"{when:%d %B %Y}", app=meta["app"], minutes=minutes,
                                           transcript="\n".join(f"[{stamp(s['start'])}] {s['who']}: {s['text']}"
                                                                for s in segs))
            text, _, err = Claude(cfg).ask(prompt, timeout=900)
            notes = text if text and not err else \
                f"# Meeting notes\n\nClaude couldn't write the summary ({err}). The transcript is saved."
        (folder / "notes.md").write_text(notes.strip() + footer, encoding="utf-8")
        if not cfg.get("keep_audio"):
            for f in ("you.wav", "others.wav"):
                (folder / f).unlink(missing_ok=True)
        status("done", notes=str(folder / "notes.md"))
    except Exception as e:
        status("error", error=f"{type(e).__name__}: {e}")


def start_processing(folder):
    """Run process() in a separate, windowless Python so Clawde stays smooth."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    subprocess.Popen([str(pyw if pyw.exists() else exe), str(HERE / "meetings.py"), "process", str(folder)],
                     creationflags=NO_WINDOW, cwd=str(HERE))


def read_status(folder):
    try:
        return json.loads((Path(folder) / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "starting"}


def new_meeting_folder(base, app):
    base = Path(base) if base else Path.home() / "Documents" / "Clawde Meetings"
    folder = base / f"{datetime.now():%Y-%m-%d %H%M} {app}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "process":
        process(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "detect":
        print(meeting_apps_in_use(), mic_users())
    else:
        print(__doc__)
