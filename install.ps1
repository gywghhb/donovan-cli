# donovan Windows install script
#
# Copy-paste install:
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/tudor-22/donovan-cli/main/install.ps1 | iex"

$RepoUrl = if ($env:DONOVAN_REPO_URL) { $env:DONOVAN_REPO_URL } else { "https://github.com/tudor-22/donovan-cli" }
$Project = "donovan-cli"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Green }
function Write-Err($msg)  { Write-Host "==> $msg" -ForegroundColor Red }

$python = $null
foreach ($candidate in @("py", "python3", "python")) {
  try {
    if ($candidate -eq "py") {
      $ver = & $candidate -3.11 --version 2>&1
      if ($LASTEXITCODE -eq 0 -and $ver -match "(\d+)\.(\d+)") {
        $python = "py -3.11"
        break
      }
    } else {
      $ver = & $candidate --version 2>&1
      if ($ver -match "(\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -ge 3 -and $minor -ge 11) {
          $python = $candidate
          break
        }
      }
    }
  } catch {
    continue
  }
}

if (-not $python) {
  Write-Err "Python 3.11+ is required but was not found."
  Write-Err "Install it from https://www.python.org/downloads/ and run this command again."
  exit 1
}

Write-Step "Found Python"

if (-not (Test-Path "pyproject.toml")) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Err "Git is required when running the installer outside a source checkout."
    Write-Err "Install Git or download the source from $RepoUrl."
    exit 1
  }

  Write-Step "Cloning donovan"
  git clone "$RepoUrl.git"
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  Set-Location $Project
}

Write-Step "Creating virtual environment"
Invoke-Expression "$python -m venv .venv"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$activate = Join-Path (Get-Location) ".venv\Scripts\Activate.ps1"
. $activate

Write-Step "Installing donovan"
python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m pip install -e .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "donovan includes optional browser automation support."
$installBrowser = Read-Host "Install browser support? [y/N]"
if ($installBrowser -eq "y" -or $installBrowser -eq "Y") {
  Write-Step "Installing browser support"
  python -m pip install -e ".[browser]"
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  python -m playwright install chromium
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Step "Running first-time setup"
donovanagent setup
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "donovan installed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "To start donovan:"
Write-Host "  .venv\Scripts\Activate.ps1"
Write-Host "  donovanagent"
Write-Host ""
Write-Host 'Or run a one-off command:'
Write-Host '  donovanagent chat "What can you do?"'
