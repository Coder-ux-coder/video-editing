# Cutroom setup for Windows (PowerShell 5+).
# Finds or installs Python 3.12, then runs setup\bootstrap.py, which installs
# FFmpeg and the Python dependencies and runs `cutroom doctor`.
#   .\setup.ps1            interactive
#   .\setup.ps1 --yes      answer yes to install prompts
#   .\setup.ps1 --dry-run  show what would happen
# If scripts are blocked: powershell -ExecutionPolicy Bypass -File .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:PYTHONUTF8 = "1"

function Find-Python312 {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $p = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
    }
    foreach ($c in @("python3.12", "python")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) {
            $v = & $c -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($v -and $v.Trim() -eq "3.12") { return (& $c -c "import sys; print(sys.executable)").Trim() }
        }
    }
    return $null
}

$py = Find-Python312
if (-not $py) {
    Write-Host "[setup] Python 3.12 not found."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = if ($args -contains "--yes") { "y" } else { Read-Host "[setup] Install Python 3.12 with winget? [y/N]" }
        if ($answer -match "^(y|yes)$") {
            winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            $py = Find-Python312
        }
    }
    if (-not $py) {
        Write-Host "[setup] Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add to PATH') and re-run."
        exit 1
    }
}

Write-Host "[setup] Using $py"
& $py setup\bootstrap.py @args
exit $LASTEXITCODE
