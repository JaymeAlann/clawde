param([switch]$Uninstall)

# Adds (or removes) a Startup-folder shortcut so Clawde launches at login.
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Clawde.lnk'

if ($Uninstall) {
    if (Test-Path $lnk) { Remove-Item $lnk -Confirm:$false }
    Write-Output 'Clawde will no longer start at login.'
    return
}

$pyw = (Get-Command pyw -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source }
if (-not $pyw) { throw 'Could not find pyw.exe or pythonw.exe. Install Python from python.org first.' }

$here = $PSScriptRoot
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = "`"$here\crab_pet.py`""
$s.WorkingDirectory = $here
$s.Description = 'Clawde desktop crab'
$s.Save()
Write-Output "Clawde will start at login ($lnk)."
