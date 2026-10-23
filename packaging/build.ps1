# Builds build\installer\Clawde-Setup-<version>.exe
# Needs: Python with requirements-meetings.txt, pyinstaller and pillow installed, and Inno Setup 6.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# on GitHub Actions use the Python that setup-python installed (the runner's `py` points elsewhere)
$py = if ($env:pythonLocation) { Join-Path $env:pythonLocation 'python.exe' }
      elseif (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
$version = (Select-String -Path crab_pet.py -Pattern '^__version__ = "(.+)"').Matches[0].Groups[1].Value
Write-Output "Building Clawde $version"

& $py packaging\make_icon.py
if ($LASTEXITCODE) { throw 'icon failed' }

Remove-Item -Recurse -Force build\dist, build\work -ErrorAction SilentlyContinue

# the app: a windowed one-folder build
& $py -m PyInstaller --noconfirm --clean --windowed --name Clawde `
    --icon "$root\packaging\clawde.ico" --distpath build\dist --workpath build\work --specpath build `
    --hidden-import crab_pet --hidden-import meetings --hidden-import claude_link `
    --collect-data faster_whisper --collect-binaries ctranslate2 --collect-binaries pyaudiowpatch `
    crab_pet.py
if ($LASTEXITCODE) { throw 'PyInstaller (Clawde) failed' }

# the hook Claude Code runs: a tiny console build, kept in its own folder
& $py -m PyInstaller --noconfirm --console --name clawde-hook `
    --distpath build\dist\Clawde\hook-build --workpath build\work --specpath build `
    clawde_hook.py
if ($LASTEXITCODE) { throw 'PyInstaller (hook) failed' }
Move-Item build\dist\Clawde\hook-build\clawde-hook build\dist\Clawde\hook
Remove-Item build\dist\Clawde\hook-build

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 not found (winget install JRSoftware.InnoSetup)' }
& $iscc "/DAppVersion=$version" packaging\clawde.iss
if ($LASTEXITCODE) { throw 'Inno Setup failed' }
Get-ChildItem build\installer\*.exe | Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } }
