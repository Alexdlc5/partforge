# PartForge one-time setup. Run setup.bat (double-click) after cloning or unzipping.
# Checks Python, FreeCAD and Ollama, offers to install what's missing with winget (asks first),
# downloads an AI model sized for this PC's graphics card, and adds Start Menu + Desktop shortcuts.
$ErrorActionPreference = "Continue"   # "Stop" turns harmless stderr from native tools into errors on PowerShell 5.1
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Ask($q) { (Read-Host "$q [Y/n]") -notmatch '^[nN]' }
function Have-Winget { [bool](Get-Command winget -ErrorAction SilentlyContinue) }
function Winget-Install($id, $what) {
    if (-not (Have-Winget)) { Write-Host "  winget isn't available. Install $what yourself, then run setup again."; return $false }
    if (-not (Ask "  Install $what now with winget?")) { return $false }
    winget install --id $id -e --accept-package-agreements --accept-source-agreements
    return $LASTEXITCODE -eq 0
}
function Find-Python {
    foreach ($c in @("py", "python")) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike "*WindowsApps*") {
            $v = & $c -c "import sys, tkinter; print(sys.version_info >= (3, 10))" 2>$null
            if ($v -eq "True") { return $cmd.Source }
        }
    }
    $p = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
    if ($p) { return $p.FullName } else { return $null }
}

Write-Host "`nPartForge setup`n===============`n"

# 1. Python 3.10+ with Tk
$python = Find-Python
if (-not $python) {
    Write-Host "Python 3.10+ not found."
    if (Winget-Install "Python.Python.3.12" "Python 3.12") { $python = Find-Python }
    if (-not $python) { Write-Host "Install Python from python.org (tick 'Add to PATH'), then run setup again."; Read-Host "Press Enter to close"; exit 1 }
}
Write-Host "OK  Python    $python"

# 2. FreeCAD
$fc = Get-ChildItem "$env:ProgramFiles\FreeCAD*\bin\freecadcmd.exe", "$env:LOCALAPPDATA\Programs\FreeCAD*\bin\freecadcmd.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $fc) {
    Write-Host "FreeCAD not found (PartForge builds every part with it)."
    Winget-Install "FreeCAD.FreeCAD" "FreeCAD" | Out-Null
} else { Write-Host "OK  FreeCAD   $($fc.FullName)" }

# 3. Ollama (the local AI server)
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama -and (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
if (-not $ollama) {
    Write-Host "Ollama not found (it runs the AI on this PC; nothing goes to the cloud)."
    if (Winget-Install "Ollama.Ollama" "Ollama") { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
}
if ($ollama -and (Test-Path $ollama)) {
    Write-Host "OK  Ollama    $ollama"
    try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3 | Out-Null }
    catch { Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep -Seconds 4 }

    # 4. A design model sized for the graphics card
    $vram = 0
    try { $vram = [int](nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | Select-Object -First 1) } catch {}
    $model = if ($vram -ge 10000) { "qwen2.5-coder:14b" } elseif ($vram -ge 6000) { "qwen2.5-coder:7b" } else { "qwen2.5-coder:3b" }
    $have = (& $ollama list 2>$null) -join "`n"
    if ($have -match [regex]::Escape($model)) { Write-Host "OK  AI model  $model" }
    elseif (Ask "Download the AI model $model (recommended for $([math]::Round($vram/1024)) GB of graphics memory)?") { & $ollama pull $model }
    if (-not ($have -match "vl|vision") -and (Ask "Optional: download qwen2.5vl:7b so the AI can look at photos of your prints (about 6 GB)?")) { & $ollama pull qwen2.5vl:7b }
}

# 5. Optional Pillow (photo thumbnails) and shortcuts
& $python -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0 -and (Ask "Optional: install Pillow for print-photo thumbnails (pip install pillow)?")) { & $python -m pip install --user pillow }
& $python "$here\install.py"

Write-Host "`nDone. Start PartForge from the Desktop or Start Menu shortcut."
Read-Host "Press Enter to close"
