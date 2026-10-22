<#
.SYNOPSIS
  powertools feasibility checker for Windows, for a normal user without admin rights.
.DESCRIPTION
  1. Finds Python 3.10+.
  2. Creates checks\.venv and installs the packages from checks\requirements.txt (uses the system proxy).
  3. Runs python -m checks.run. The report lands in checks\out\report-<time>.txt and .json.
     The report holds only statuses, counts, HTTP codes and device names: safe to paste into a chat.
.EXAMPLE
  check.cmd                                   (all checks except the Whisper load test)
  check.cmd -Only meeting                     (run during a Teams meeting, then again outside one)
  check.cmd -Only graph,audio
  check.cmd -Only whisper -Whisper            (installs faster-whisper + CUDA libs into checks\.venv, ~1.5 GB)
  check.cmd -WhisperVenv C:\path\to\letmetalk\.venv   (reuse an existing venv for the Whisper test)
  check.cmd -Dry                              (non-interactive smoke run, used by CI)
#>
[CmdletBinding()]
param(
  [string]$Only = '',
  [switch]$Whisper,
  [string]$WhisperVenv = '',
  [string]$Model = 'turbo',
  [switch]$Dry
)

$ErrorActionPreference = 'Stop'
$ChecksDir = $PSScriptRoot
$Repo = Split-Path -Parent $ChecksDir
$Venv = Join-Path $ChecksDir '.venv'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { Write-Verbose 'TLS setting unchanged' }

function Invoke-Native {
  # Windows PowerShell 5.1 turns any stderr line of a native program into a terminating error
  # while ErrorActionPreference is Stop, so run natives with Continue and return the exit code.
  param([string]$Exe, [string[]]$ArgList)
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $Exe @ArgList 2>&1 | ForEach-Object { "$_" } | Out-Host; return $LASTEXITCODE }
  finally { $ErrorActionPreference = $old }
}

function Find-Python {
  $candidates = @(
    (Join-Path $env:LOCALAPPDATA 'powertools\python312\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\python.exe')
  )
  foreach ($c in @('python', 'py')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { $candidates += $cmd.Source }
  }
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    foreach ($exe in $candidates) {
      if (-not (Test-Path $exe)) { continue }
      # No double quotes in the code: Windows PowerShell 5.1 strips them from native arguments.
      $pyArgs = @('-c', 'import sys; print(sys.version_info[:2])')
      if ((Split-Path $exe -Leaf) -eq 'py.exe') { $pyArgs = @('-3') + $pyArgs }
      $out = (& $exe @pyArgs 2>&1 | ForEach-Object { "$_" }) -join "`n"
      if ($out -match '\((\d+), (\d+)\)' -and [int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 10) {
        if ((Split-Path $exe -Leaf) -eq 'py.exe') {
          $real = (& $exe -3 -c 'import sys; print(sys.executable)' 2>&1 | Select-Object -First 1)
          return "$real".Trim()
        }
        return $exe
      }
      Write-Host "  skipped $exe ($out)"
    }
  } finally { $ErrorActionPreference = $old }
  return $null
}

$env:POWERTOOLS_PS_VERSION = $PSVersionTable.PSVersion.ToString()
try { $env:POWERTOOLS_EXEC_POLICY = (Get-ExecutionPolicy).ToString() } catch { $env:POWERTOOLS_EXEC_POLICY = 'unknown' }
$env:PYTHONIOENCODING = 'utf-8'
# An embeddable Python (python312._pth) ignores the current folder and PYTHONPATH, so 'python -m checks.run' cannot
# find the package. Put the repository on sys.path ourselves. No double quotes: PS 5.1 strips them from arguments.
$Boot = "import sys, runpy; sys.path.insert(0, r'$Repo'); runpy.run_module('checks.run', run_name='__main__', alter_sys=True)"

Write-Host ''
Write-Host 'powertools feasibility checker (no admin rights needed)' -ForegroundColor Cyan
Write-Host "Repository: $Repo"
Write-Host ''

Push-Location $Repo
try {
  if ($WhisperVenv) {
    $wpy = Join-Path $WhisperVenv 'Scripts\python.exe'
    if (-not (Test-Path $wpy)) { throw "No python.exe in $WhisperVenv\Scripts" }
    Write-Host "Using the existing venv for the Whisper test: $WhisperVenv"
    $ErrorActionPreference = 'Continue'
    & $wpy -c $Boot --only whisper --whisper --model $Model
    exit $LASTEXITCODE
  }

  $vpy = Join-Path $Venv 'Scripts\python.exe'
  if (-not (Test-Path $vpy)) {
    $py = Find-Python
    if (-not $py) {
      Write-Host 'No Python 3.10+ found. Run powertools.cmd once (its setup installs Python per-user) or install' -ForegroundColor Red
      Write-Host 'Python per-user from python.org (no admin needed), then run check.cmd again.' -ForegroundColor Red
      exit 2
    }
    Write-Host "Python: $py"
    $code = Invoke-Native $py @('-m', 'venv', $Venv)
    if ($code -ne 0 -or -not (Test-Path $vpy)) {
      Write-Host 'venv module missing (embeddable Python?), trying virtualenv ...' -ForegroundColor Yellow
      $null = Invoke-Native $py @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', 'virtualenv')
      $code = Invoke-Native $py @('-m', 'virtualenv', $Venv)
      if ($code -ne 0) { throw 'Could not create checks\.venv' }
    }
  }

  Write-Host 'Installing packages (quiet; first run takes a minute) ...'
  $pkgs = @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-r', (Join-Path $ChecksDir 'requirements.txt'))
  $code = Invoke-Native $vpy $pkgs
  if ($code -ne 0) { Write-Host '[WARN] pip install failed; checks that need a missing package will report it.' -ForegroundColor Yellow }
  if ($Whisper) {
    Write-Host 'Installing faster-whisper + CUDA libraries (~1.5 GB) ...'
    $code = Invoke-Native $vpy @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', 'faster-whisper', 'nvidia-cublas-cu12', 'nvidia-cudnn-cu12')
    if ($code -ne 0) { Write-Host '[WARN] faster-whisper install failed (see above).' -ForegroundColor Yellow }
  }

  $runArgs = @('-c', $Boot, '--model', $Model)
  if ($Only) { $runArgs += @('--only', $Only) }
  if ($Whisper) { $runArgs += '--whisper' }
  if ($Dry) { $runArgs += '--dry' }
  # Run Python directly on the console (not through a pipe): the checks are interactive.
  $ErrorActionPreference = 'Continue'
  & $vpy @runArgs
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
