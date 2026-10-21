<div align="center">

# Clawde 🦀

**A pixel-art desktop crab for Windows that stays on top of your windows and reacts to what [Claude Code](https://claude.com/claude-code) is doing.**

![Clawde working, celebrating, and showing its hover menu](docs/hero.gif)

</div>

---

## Contents

- [Features](#features)
- [Moods](#moods)
- [Requirements](#requirements)
- [Installation](#installation)
- [Using Clawde](#using-clawde)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Uninstalling](#uninstalling)
- [Development](#development)
- [Privacy](#privacy)
- [License](#license)

## Features

- 🪟 **Always on top.** Clawde floats above every app, browser and editor while you work.
- 🖱️ **Drag it anywhere.** Click and hold to carry it to any monitor (its legs dangle). It remembers where you dropped it.
- 🎲 **A mind of its own.** It idles, wanders around, looks around, waves, dances, blows bubbles and snaps its claws.
- 😴 **Naps.** After a few minutes with no keyboard or mouse input it falls asleep, and it wakes up happy when you come back.
- 🤩 **Gets excited when Claude is thinking.** Whenever *any* Claude Code session has just been given a prompt.
- 💻 **Pulls out a laptop when Claude is working.** Whenever *any* Claude Code session is running tools, on Windows or in WSL. It celebrates when the work is done.
- 💡 **Offers help.** When it looks like you're stuck, Clawde pops up with a short tip from Claude.
- 🎙️ **Voice chat with Claude Code.** Hover the crab, press the microphone button, and talk.

## Moods

| Idle | Walking | Sleeping | Thinking | Working |
| :---: | :---: | :---: | :---: | :---: |
| ![idle](docs/idle.gif) | ![walk](docs/walk.gif) | ![sleep](docs/sleep.gif) | ![think](docs/think.gif) | ![work](docs/work.gif) |
| Breathing, blinking | Wandering your screen | No input for 3 min | Claude got a prompt | Claude is running tools |

| Being dragged | Happy | Dancing | Bubbles | Has a tip |
| :---: | :---: | :---: | :---: | :---: |
| ![drag](docs/drag.gif) | ![happy](docs/happy.gif) | ![dance](docs/dance.gif) | ![bubble](docs/bubble.gif) | ![alert](docs/alert.gif) |
| Click and hold | Clicked, or Claude finished | Random | Random | Thinks you need help |

All the poses on one sheet:

![All poses](docs/poses.png)

## Requirements

| | |
| --- | --- |
| **OS** | Windows 10 or 11 |
| **Python** | 3.9 or newer, with tkinter (the python.org installer includes it) |
| **Claude Code** | Installed and logged in, either on Windows or inside WSL |
| **Voice input** (optional) | Windows voice typing (Win+H), which is built into Windows 10/11 |

There are no pip packages to install. Clawde only uses the Python standard library.

## Installation

### 1. Install Python

Download it from [python.org](https://www.python.org/downloads/windows/) and tick **"Add python.exe to PATH"**, or install it with winget:

```powershell
winget install Python.Python.3.13
```

Check that it works (this should print `8.6`):

```powershell
py -c "import tkinter; print(tkinter.TkVersion)"
```

### 2. Install Claude Code and log in

Clawde talks to the `claude` command. You can install it on Windows, inside WSL, or both. Install it in just one of the following ways.

**On Windows (PowerShell):**

```powershell
irm https://claude.ai/install.ps1 | iex
```

**Inside WSL (Ubuntu):**

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Then run `claude` once in that same environment and log in. See the [Claude Code docs](https://docs.claude.com/en/docs/claude-code/setup) for other install options.

### 3. Get Clawde

```powershell
git clone https://github.com/JaymeAlann/clawde.git
cd clawde
```

You can also download the repository as a ZIP from GitHub and extract it.

### 4. Run it

```powershell
pyw crab_pet.py
```

Or double-click **`Start Clawde.bat`**. Clawde appears near the bottom-right corner of your main screen and says hi.

### 5. (Optional) Start Clawde when you log in

```powershell
powershell -ExecutionPolicy Bypass -File install_autostart.ps1
```

This adds a shortcut to your Windows Startup folder.

## Using Clawde

<img src="docs/menu.png" alt="Hover menu" width="160" align="right">

| Action | What happens |
| --- | --- |
| **Hover** over the crab | The menu appears underneath it, and hides again when you move away |
| **Terminal button** (left) | Opens a new Windows Terminal tab running `claude` |
| **Microphone button** (right) | Opens the chat window and starts voice typing. Talk, then pause: your message is sent to Claude Code automatically |
| **Click and hold** | Drag Clawde anywhere, across monitors |
| **Click** | Clawde is happy to see you (and wakes up if it was asleep) |
| **Click a speech bubble** | Opens the chat about that tip |
| **Right-click** | Menu: type to chat, open the CLI, turn wandering on/off, turn smart tips on/off, take a nap, quit |

**The chat window** keeps the conversation going until you press **New chat**. Each message is sent with `claude -p --resume <session>`, so Claude Code remembers what you said earlier. You can also type and press Enter instead of talking.

**When Clawde offers help:**

- The title of the window you're on contains words like *error*, *exception*, *traceback*, *failed*, *how to*, *404*... Clawde shows a ❗ and asks Claude's Haiku model for a short, specific tip.
- You're switching windows a lot. Clawde offers to help you find what you're looking for.
- You've been working for about an hour. Clawde reminds you to stretch.
- Every now and then, it shares a Claude Code tip.

It never shows more than one tip every 5 minutes, and stays quiet while it's asleep or being dragged.

<br clear="right">

## Configuration

`crab_config.json` is created next to `crab_pet.py` the first time Clawde runs. Edit it, then restart Clawde.

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | `"Clawde"` | The crab's name, shown in bubbles and the chat window |
| `claude_mode` | `"auto"` | Which Claude Code CLI to use: `auto`, `windows` or `wsl`. `auto` uses `claude` on your Windows PATH if there is one, otherwise the one in WSL |
| `wsl_distro` | `"Ubuntu"` | The WSL distro to use for the CLI and to watch for activity |
| `workdir` | your home folder | The folder Claude Code starts in (Windows mode; WSL always starts in `~`) |
| `ai_tips` | `true` | Ask Claude for a tip when you look stuck. When off, Clawde shows a generic offer to help instead |
| `sleep_after_sec` | `180` | Seconds without input before Clawde naps |
| `walk` | `true` | Let Clawde wander around |
| `scale` | `0` | Size of one sprite pixel in screen pixels; `0` picks one from your display's DPI |
| `position` | `null` | Last place you dropped Clawde (saved automatically) |

## How it works

- **The window** is a borderless, always-on-top tkinter window. A colour key makes everything except the crab transparent and click-through.
- **Activity detection.** Clawde doesn't need any Claude Code hooks or settings changes. It watches the session transcripts that Claude Code already writes to `%USERPROFILE%\.claude\projects` and, through `\\wsl.localhost\<distro>\home\*\.claude\projects`, to WSL. If the newest entry in a session is a prompt, Clawde shows **thinking**. If it's a tool call or tool result, it shows **working**.
- **Idle and help detection** use the Win32 `GetLastInputInfo` and `GetForegroundWindow` functions through `ctypes`.
- **Voice input** focuses the chat box and presses Win+H to start Windows' own voice typing. Your audio goes through Windows, not through Clawde.
- **Chat and tips** run `claude -p --output-format json` in the background.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Clawde says it can't find Claude Code | Make sure `claude --version` works on Windows, or `wsl -d Ubuntu -e bash -lc "claude --version"` works in WSL. Set `claude_mode` and `wsl_distro` to match |
| Chat replies with "Failed to authenticate" | Run `claude` in a terminal in that environment and log in again |
| Clawde never shows the laptop | Check that your Claude Code transcripts are in `~/.claude/projects`. For WSL, set `wsl_distro` to your distro's name (`wsl -l`) |
| Voice typing doesn't start | Turn on voice typing in **Settings → Time & language → Speech**, or click the 🎤 button in the chat window |
| Clawde is too big or too small | Set `scale` (try `3` to `6`) |
| A console window appears | Start Clawde with `pyw`, not `py` |
| Clawde disappeared off-screen | Delete the `position` entry in `crab_config.json` and restart |

## Uninstalling

1. Right-click Clawde and choose **Goodbye**.
2. Remove the login shortcut, if you added it:

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Uninstall
   ```

3. Delete the folder.

## Development

```text
crab_pet.py              the whole app: sprite renderer, behaviour, activity watcher, chat
install_autostart.ps1    adds or removes the login shortcut
Start Clawde.bat         double-click launcher
tools/make_docs_images.py  regenerates the images in docs/
docs/                    README images
```

The sprite is drawn in code on a 32×32 grid (see `render()` and `pose_params()`), so there are no image assets to edit. To add a pose, add a branch in `pose_params()`, then preview it:

```powershell
py crab_pet.py --preview poses.png
```

To regenerate the README images (this needs Pillow, for docs only):

```powershell
py -m pip install Pillow
py tools/make_docs_images.py
```

## Privacy

- Clawde reads your Claude Code transcript files **locally**, only to tell whether a session is busy. Their contents never leave your machine.
- With `ai_tips` on, the **title of your active window** is sent to Claude when it looks like you need help. Window titles can contain email subjects, document names or URLs. Turn this off from the right-click menu, or set `"ai_tips": false`.
- Chat messages go to Claude Code, exactly as if you'd typed them into `claude`.

## License

[MIT](LICENSE)
