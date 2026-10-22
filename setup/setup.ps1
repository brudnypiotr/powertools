<#
.SYNOPSIS
  powertools setup for Windows, for a normal user without admin rights. Safe to run again.
.DESCRIPTION
  1. Finds Python 3.11+ (any, the embeddable one too: the overlay uses Win32 directly, not tkinter); installs
     Python 3.12 per-user from python.org only when there is none.
  2. Creates .venv and installs requirements.txt (faster-whisper + CUDA libraries, ~1.5 GB the first time).
  3. Creates the shortcuts (desktop, Start menu) and, unless -NoAutostart, a Startup entry.
  4. Runs the self-test. Writes setup.log; records setup\version.txt in local\setup-version.txt.
.EXAMPLE
  powertools.cmd             runs this when needed, then starts the app
  setup.cmd -NoAutostart     (or: powershell -File setup\setup.ps1 -NoAutostart)
#>
[CmdletBinding()]
param([switch]$NoAutostart)

$ErrorActionPreference = 'Stop'
$ScriptDir = $PSScriptRoot
$Repo = Split-Path -Parent $ScriptDir
$Base = Join-Path $env:LOCALAPPDATA 'powertools'
$Downloads = Join-Path $Base 'downloads'
$Venv = Join-Path $Repo '.venv'
$Sources = Get-Content (Join-Path $ScriptDir 'sources.json') -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $Base, $Downloads, (Join-Path $Repo 'local') | Out-Null
try { Start-Transcript -Path (Join-Path $Repo 'setup.log') -Force | Out-Null } catch { Write-Verbose 'no transcript' }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { Write-Verbose 'TLS unchanged' }

function Step([string]$Name, [string]$Status, [string]$Detail) {
  $color = switch ($Status) { 'OK' { 'Green' } 'SKIP' { 'Gray' } 'WARN' { 'Yellow' } default { 'Red' } }
  Write-Host ("[{0,-4}] {1}: {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Invoke-Native {
  # Windows PowerShell 5.1 turns stderr lines of native programs into errors under Stop: run with Continue.
  param([string]$Exe, [string[]]$ArgList)
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $Exe @ArgList 2>&1 | ForEach-Object { "$_" } | Out-Host; return $LASTEXITCODE }
  finally { $ErrorActionPreference = $old }
}

function Test-Python([string]$Exe) {
  # Returns @{exe; version; tk} or $null. No double quotes in the code: PS 5.1 strips them from arguments.
  if (-not (Test-Path $Exe)) { return $null }
  $code = 'import sys; v=sys.version_info; ok=1' + "`n" + 'try:' + "`n" + ' import tkinter' + "`n" + 'except Exception: ok=0' + "`n" + 'print(v[0], v[1], ok, sys.executable)'
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $out = (& $Exe -c $code 2>$null | Select-Object -Last 1) } finally { $ErrorActionPreference = $old }
  if ("$out" -match '^(\d+) (\d+) (\d) (.+)$' -and [int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 11) {
    return @{ exe = $Matches[4].Trim(); version = "$($Matches[1]).$($Matches[2])"; tk = ($Matches[3] -eq '1') }
  }
  return $null
}

function Find-Python {
  $candidates = @(
    (Join-Path $Base 'python312\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
  )
  foreach ($c in @('python', 'py')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { $candidates += $cmd.Source }
  }
  $candidates += (Join-Path $env:LOCALAPPDATA 'woynich-wiki\python312\python.exe')  # embeddable: no tkinter
  $found = @($candidates | ForEach-Object { Test-Python $_ } | Where-Object { $_ })
  $full = $found | Where-Object { $_.tk } | Select-Object -First 1
  if ($full) { return $full }
  return ($found | Select-Object -First 1)
}

function Get-File([string]$Url, [string]$Sha256, [string]$Name) {
  $dest = Join-Path $Downloads $Name
  if ((Test-Path $dest) -and $Sha256 -and ((Get-FileHash $dest -Algorithm SHA256).Hash -eq $Sha256.ToUpper())) { return $dest }
  Write-Host "Downloading $Name ..."
  $proxy = [Net.WebRequest]::GetSystemWebProxy(); $proxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials
  $wc = New-Object Net.WebClient; $wc.Proxy = $proxy
  $wc.DownloadFile($Url, $dest)
  if ($Sha256 -and (Get-FileHash $dest -Algorithm SHA256).Hash -ne $Sha256.ToUpper()) { Remove-Item $dest -Force; throw "Checksum mismatch for $Name" }
  return $dest
}

Write-Host ''
Write-Host 'powertools setup (no admin rights needed)' -ForegroundColor Cyan
Write-Host "Repository: $Repo"
Write-Host ''

# ------------------------------------------------------------------ 1. Python
$py = Find-Python
if (-not $py) {
  Write-Host 'No Python 3.11+ found; installing Python 3.12 for this user (python.org, no admin) ...'
  try {
    $installer = Get-File $Sources.python_installer.url $Sources.python_installer.sha256 'python-installer.exe'
    $target = Join-Path $Base 'python312'
    $argList = @('/quiet', 'InstallAllUsers=0', "TargetDir=$target", 'PrependPath=0', 'Include_launcher=0', 'Include_test=0', 'Include_tcltk=1', 'Shortcuts=0', 'AssociateFiles=0')
    $p = Start-Process -FilePath $installer -ArgumentList $argList -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "installer exit code $($p.ExitCode)" }
    $py = Find-Python
  } catch {
    Step 'Python' 'WARN' "per-user install failed: $($_.Exception.Message)"
  }
}
if (-not $py) { Step 'Python' 'FAIL' 'no Python 3.11+ found or installable'; exit 2 }
Step 'Python' 'OK' ("{0} {1}" -f $py.version, $py.exe)

# ------------------------------------------------------------------ 2. venv + packages
$vpy = Join-Path $Venv 'Scripts\python.exe'
$recreate = (Test-Path $vpy) -and -not (Test-Python $vpy)
if ($recreate) { Remove-Item $Venv -Recurse -Force }
if (-not (Test-Path $vpy)) {
  $code = Invoke-Native $py.exe @('-m', 'venv', $Venv)
  if ($code -ne 0 -or -not (Test-Path $vpy)) {
    $null = Invoke-Native $py.exe @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', 'virtualenv')
    $code = Invoke-Native $py.exe @('-m', 'virtualenv', $Venv)
    if ($code -ne 0) { Step 'venv' 'FAIL' 'could not create .venv'; exit 2 }
  }
}
Step 'venv' 'OK' $Venv
Write-Host 'Installing packages (first time: ~1.5 GB, a few minutes) ...'
$code = Invoke-Native $vpy @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '-r', (Join-Path $Repo 'requirements.txt'))
if ($code -ne 0) { Step 'packages' 'FAIL' 'pip install failed (see above)'; exit 2 }
Step 'packages' 'OK' 'requirements.txt'

# ------------------------------------------------------------------ 3. icon + shortcuts
$icon = Join-Path $Base 'app.ico'
$boot = "import sys, runpy; sys.path.insert(0, r'$Repo'); from powertools.tray import icon_image; icon_image('idle').save(r'$icon', sizes=[(16,16),(32,32),(48,48),(64,64)])"
$null = Invoke-Native $vpy @('-c', $boot)
$launcher = Join-Path $Repo 'powertools.cmd'
$shell = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'))
$startup = [Environment]::GetFolderPath('Startup')
if (-not $NoAutostart) { $dirs += $startup } else { Remove-Item (Join-Path $startup 'powertools.lnk') -ErrorAction SilentlyContinue }
foreach ($dir in $dirs) {
  if (-not $dir -or -not (Test-Path $dir)) { continue }
  $lnk = $shell.CreateShortcut((Join-Path $dir 'powertools.lnk'))
  $lnk.TargetPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
  $lnk.Arguments = if ($dir -eq $startup) { "/c `"`"$launcher`"`" --no-browser" } else { "/c `"`"$launcher`"`"" }
  $lnk.WorkingDirectory = $Repo
  $lnk.WindowStyle = 7  # minimized: the cmd window only flashes
  if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
  $lnk.Description = 'powertools: dictation with local Whisper'
  $lnk.Save()
}
Step 'shortcuts' 'OK' ($(if ($NoAutostart) { 'desktop, Start menu' } else { 'desktop, Start menu, autostart' }))

# ------------------------------------------------------------------ 4. self-test
$selftest = "import sys, runpy; sys.path.insert(0, r'$Repo'); runpy.run_module('powertools', run_name='__main__', alter_sys=True)"
$code = Invoke-Native $vpy @('-c', $selftest, '--selftest')
if ($code -ne 0) { Step 'selftest' 'FAIL' 'see above'; exit 2 }
Step 'selftest' 'OK' 'window server and API answer'

Copy-Item (Join-Path $ScriptDir 'version.txt') (Join-Path $Repo 'local\setup-version.txt') -Force
Step 'setup' 'OK' 'done'
try { Stop-Transcript | Out-Null } catch { Write-Verbose 'no transcript' }
exit 0
