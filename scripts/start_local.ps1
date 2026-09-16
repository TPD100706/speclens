[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$projectRoot = Split-Path -Parent $PSScriptRoot
$requirementsFile = Join-Path $projectRoot "requirements.txt"
$appFile = Join-Path $projectRoot "app.py"
$venvDir = Join-Path $projectRoot ".speclens_runtime"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$requirementsStamp = Join-Path $venvDir ".requirements.sha256"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "[$([char]0x2713)] $Message" -ForegroundColor Cyan
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace("-", "")
    }
    finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

function Test-PythonCandidate {
    param(
        [string]$Executable,
        [string[]]$PrefixArguments = @()
    )
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf) -and -not (Get-Command $Executable -ErrorAction SilentlyContinue)) {
        return $false
    }
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Executable @PrefixArguments -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $savedPreference
    }
}

function Find-CompatiblePython {
    $candidates = @()
    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $candidates += [pscustomobject]@{ Executable = $pyLauncher.Source; PrefixArguments = @("-3") }
    }
    $pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $candidates += [pscustomobject]@{ Executable = $pythonCommand.Source; PrefixArguments = @() }
    }
    $pythonInstallRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path -LiteralPath $pythonInstallRoot) {
        $installedPython = Get-ChildItem -LiteralPath $pythonInstallRoot -Filter "python.exe" -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending
        foreach ($item in $installedPython) {
            $candidates += [pscustomobject]@{ Executable = $item.FullName; PrefixArguments = @() }
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-PythonCandidate -Executable $candidate.Executable -PrefixArguments $candidate.PrefixArguments) {
            return $candidate
        }
    }
    return $null
}

function Install-PythonIfNeeded {
    $candidate = Find-CompatiblePython
    if ($candidate) {
        return $candidate
    }

    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.10+ was not found and winget is unavailable. Install Python 3.10+ with Add Python to PATH enabled, then try again."
    }

    Write-Step "Python 3.10+ was not found. Installing Python 3.12 for the current user"
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $winget.Source install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements --silent
    $installExitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedPreference
    if ($installExitCode -ne 0) {
        throw "Python installation failed with exit code $installExitCode. Check the network or install Python 3.10-3.13 manually."
    }

    $candidate = Find-CompatiblePython
    if (-not $candidate) {
        throw "Python was installed but is not visible yet. Double-click Start_SpecLens.bat again."
    }
    return $candidate
}

function Test-PortAvailable {
    param([int]$Port)
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        try { $listener.Stop() } catch { }
    }
}

try {
    Set-Location -LiteralPath $projectRoot
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host "  SpecLens local setup and launcher" -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor DarkCyan

    if (-not (Test-Path -LiteralPath $requirementsFile -PathType Leaf) -or -not (Test-Path -LiteralPath $appFile -PathType Leaf)) {
        throw "Project files are incomplete. Keep app.py, requirements.txt, and the launcher in the same project folder."
    }

    $python = Install-PythonIfNeeded
    Write-Step "Python environment is ready"

    $venvIsUsable = $false
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $venvPython -c "import sys, pip; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)" 2>$null
        $venvIsUsable = $LASTEXITCODE -eq 0
        $ErrorActionPreference = $savedPreference
    }

    if (-not $venvIsUsable) {
        Write-Step "First run: creating the project virtual environment"
        if (Test-Path -LiteralPath $venvDir) {
            Remove-Item -LiteralPath $venvDir -Recurse -Force
        }
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $python.Executable @($python.PrefixArguments) -m venv $venvDir
        $venvExitCode = $LASTEXITCODE
        $ErrorActionPreference = $savedPreference
        if ($venvExitCode -ne 0 -or -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "Failed to create the virtual environment."
        }
    }
    else {
        Write-Step "Existing project virtual environment found"
    }

    $requirementsHash = Get-Sha256Hex -Path $requirementsFile
    $savedHash = if (Test-Path -LiteralPath $requirementsStamp) {
        (Get-Content -Raw -LiteralPath $requirementsStamp).Trim()
    } else { "" }

    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $venvPython -c "import streamlit, pandas, pymupdf, openpyxl, openai, dotenv" 2>$null
    $dependenciesReady = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $savedPreference

    if (-not $dependenciesReady -or $requirementsHash -ne $savedHash) {
        Write-Step "Installing or updating project dependencies (internet access is required)"
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $venvPython -m pip install --disable-pip-version-check --upgrade pip
        $pipUpgradeExitCode = $LASTEXITCODE
        if ($pipUpgradeExitCode -eq 0) {
            & $venvPython -m pip install --disable-pip-version-check -r $requirementsFile
            $dependencyExitCode = $LASTEXITCODE
        } else {
            $dependencyExitCode = 1
        }
        $ErrorActionPreference = $savedPreference
        if ($pipUpgradeExitCode -ne 0) { throw "Failed to update pip." }
        if ($dependencyExitCode -ne 0) { throw "Failed to install dependencies. Check the network and try again." }
        Set-Content -LiteralPath $requirementsStamp -Value $requirementsHash -Encoding ASCII
    }
    else {
        Write-Step "Project dependencies are ready"
    }

    $port = $null
    foreach ($candidatePort in 8501..8510) {
        if (Test-PortAvailable -Port $candidatePort) {
            $port = $candidatePort
            break
        }
    }
    if (-not $port) {
        throw "Ports 8501-8510 are all in use. Stop an existing service and try again."
    }

    $localUrl = "http://localhost:$port"
    Write-Step "Setup complete. Starting SpecLens"
    Write-Host ""
    Write-Host "Local URL: $localUrl" -ForegroundColor Green
    Write-Host "The browser will open when the service is ready. Close this window to stop the service." -ForegroundColor Gray
    Write-Host ""

    $streamlitArguments = @(
        "-m", "streamlit", "run", "app.py",
        "--server.address", "localhost",
        "--server.port", "$port",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false"
    )
    $serverProcess = Start-Process -FilePath $venvPython -ArgumentList $streamlitArguments -WorkingDirectory $projectRoot -NoNewWindow -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($serverProcess.HasExited) { break }
        try {
            $health = Invoke-WebRequest -Uri "$localUrl/_stcore/health" -UseBasicParsing -TimeoutSec 1
            if ($health.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch { }
        Start-Sleep -Milliseconds 500
    }

    if (-not $ready) {
        if (-not $serverProcess.HasExited) { Stop-Process -Id $serverProcess.Id -Force }
        throw "The service did not become ready within 30 seconds. Review the log above."
    }

    if (-not $NoBrowser) {
        Start-Process $localUrl
    }
    Write-Host "SpecLens is running." -ForegroundColor Green
    Wait-Process -Id $serverProcess.Id
    exit $serverProcess.ExitCode
}
catch {
    Write-Host ""
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
