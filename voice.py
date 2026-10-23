"""Clawde's voice: reading text aloud and listening for "Hey Clawde".

Both use Windows' own speech engines (System.Speech) through a hidden
PowerShell, so everything stays on the PC and no extra packages are needed.
"""
import os
import re
import subprocess
import threading

NO_WINDOW = 0x08000000

SPEAK = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($env:CLAWDE_VOICE) { try { $s.SelectVoice($env:CLAWDE_VOICE) } catch {} }
$s.Rate = 1
$s.Speak([Console]::In.ReadToEnd())
"""

LISTEN = r"""
Add-Type -AssemblyName System.Speech
$parent = [int]$env:CLAWDE_PID
$r = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$phrases = New-Object System.Speech.Recognition.Choices
foreach ($p in $env:CLAWDE_WAKE.Split('|')) { $phrases.Add($p) }
$r.LoadGrammar((New-Object System.Speech.Recognition.Grammar((New-Object System.Speech.Recognition.GrammarBuilder($phrases)))))
$r.SetInputToDefaultAudioDevice()
[Console]::Out.WriteLine('READY'); [Console]::Out.Flush()
while ($true) {
    $res = $r.Recognize([TimeSpan]::FromSeconds(3))
    if ($res -and $res.Confidence -ge [double]$env:CLAWDE_CONFIDENCE) {
        [Console]::Out.WriteLine('WAKE ' + $res.Text); [Console]::Out.Flush()
    }
    if (-not (Get-Process -Id $parent -ErrorAction SilentlyContinue)) { break }
}
"""


def _powershell(script, env, **kw):
    return subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-Command", script], env={**os.environ, **env}, creationflags=NO_WINDOW,
                            text=True, encoding="utf-8", **kw)


def speakable(text, limit=600):
    """Strip markdown and code so it reads naturally; long replies get cut short."""
    text = re.sub(r"```.*?```", " (code in the chat window) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_#>|]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit].rsplit(". ", 1)[0]
        text = cut + ". The rest is in the chat window."
    return text


class Speaker:
    def __init__(self):
        self.proc = None

    def say(self, text, voice=""):
        self.stop()
        text = speakable(text)
        if not text:
            return
        try:
            self.proc = _powershell(SPEAK, {"CLAWDE_VOICE": voice or ""}, stdin=subprocess.PIPE)
            self.proc.stdin.write(text)
            self.proc.stdin.close()
        except OSError:
            self.proc = None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None


class WakeListener:
    """Runs Windows speech recognition on a tiny grammar ("hey clawde") and calls on_wake."""

    def __init__(self, name, on_wake, on_status, confidence=0.6):
        self.name = name
        self.on_wake = on_wake
        self.on_status = on_status
        self.confidence = confidence
        self.proc = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.running:
            return
        n = self.name.lower()
        phrases = sorted({f"hey {n}", "hey clawde", "hey claude", "okay claude"})
        env = {"CLAWDE_PID": str(os.getpid()), "CLAWDE_WAKE": "|".join(phrases),
               "CLAWDE_CONFIDENCE": str(self.confidence)}
        try:
            self.proc = _powershell(LISTEN, env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as e:
            self.on_status(f"I can't listen: {e}")
            return
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if line == "READY":
                self.on_status(None)
            elif line.startswith("WAKE"):
                self.on_wake(line[5:])
        if proc is self.proc:                   # ended without being stopped
            err = (proc.stderr.read() or "").strip().splitlines()
            self.on_status("I stopped listening for my name" + (f": {err[-1]}" if err else "."))

    def stop(self):
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.kill()
