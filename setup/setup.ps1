<#
.SYNOPSIS
  powertools setup for Windows, for a normal user without admin rights. Safe to run again.
.DESCRIPTION
  1. Finds Python 3.11+ (any, the embeddable one too: the overlay uses Win32 directly, not tkinter); installs
     Python 3.12 per-user from python.org only when there is none.
  2. Creates .venv and installs requirements.txt (faster-whisper + CUDA libraries, ~1.5 GB the first time).
     Unpacks Portable Git to %LOCALAPPDATA%\powertools\git when there is no git (Update in the app needs it).
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
  $candidates += (Join-Path $Base 'python312-embed\python.exe')
  $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe')
  foreach ($c in @('python', 'py')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { $candidates += $cmd.Source }
  }
  # every Python registered by an installer (PEP 514), wherever it was put: a reinstall of a version that is already
  # registered keeps the old folder and ignores TargetDir
  foreach ($root in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
    foreach ($ver in @(Get-ChildItem $root -ErrorAction SilentlyContinue)) {
      $ip = Get-ItemProperty (Join-Path $ver.PSPath 'InstallPath') -ErrorAction SilentlyContinue
      if (-not $ip) { continue }
      if ($ip.ExecutablePath) { $candidates += $ip.ExecutablePath }
      elseif ($ip.'(default)') { $candidates += (Join-Path $ip.'(default)' 'python.exe') }
    }
  }
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
  $instLog = Join-Path $Base 'python-install.log'
  try {
    $installer = Get-File $Sources.python_installer.url $Sources.python_installer.sha256 'python-installer.exe'
    $target = Join-Path $Base 'python312'
    $argList = @('/quiet', '/log', "`"$instLog`"", 'InstallAllUsers=0', "TargetDir=$target", 'PrependPath=0', 'Include_launcher=0', 'Include_test=0', 'Include_tcltk=1', 'Shortcuts=0', 'AssociateFiles=0')
    for ($try = 1; $try -le 4; $try++) {
      $p = Start-Process -FilePath $installer -ArgumentList $argList -Wait -PassThru
      if ($p.ExitCode -ne 1618) { break }
      # 1618 = another installation (Windows Update, Intune, ...) is running: wait and retry
      Write-Host "Another installation is in progress (code 1618); waiting 45 s (attempt $try of 4) ..." -ForegroundColor Yellow
      Start-Sleep -Seconds 45
    }
    if ($p.ExitCode -ne 0) { throw "installer exit code $($p.ExitCode) (log: $instLog)" }
    $py = Find-Python
    if (-not $py) { throw "the installer finished but no Python appeared (log: $instLog)" }
  } catch {
    Step 'Python' 'WARN' "per-user install: $($_.Exception.Message); trying the embeddable package"
  }
}
if (-not $py) {
  try {
    $target = Join-Path $Base 'python312-embed'
    $zip = Get-File $Sources.python_embed.url $Sources.python_embed.sha256 'python-embed.zip'
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $target -Force
    $pth = Join-Path $target 'python312._pth'
    (Get-Content $pth) -replace '^#\s*import site', 'import site' | Set-Content $pth -Encoding ASCII
    Add-Content $pth 'Lib\site-packages'
    $getpip = Get-File $Sources.get_pip.url $null 'get-pip.py'
    $null = Invoke-Native (Join-Path $target 'python.exe') @($getpip, '--no-warn-script-location')
    $py = Find-Python
  } catch {
    Step 'Python' 'WARN' "embeddable package failed: $($_.Exception.Message)"
  }
}
if (-not $py) { Step 'Python' 'FAIL' 'no Python 3.11+ found or installable'; exit 2 }
Step 'Python' 'OK' ("{0} {1}" -f $py.version, $py.exe)

# ------------------------------------------------------------------ 2. venv + packages
$vpy = Join-Path $Venv 'Scripts\python.exe'
$cfg = Join-Path $Venv 'pyvenv.cfg'
# a venv built on another app's Python breaks when that app is removed: powertools keeps its own
$foreign = (Test-Path $cfg) -and ((Get-Content $cfg -Raw) -match 'woynich-wiki')
$recreate = (Test-Path $vpy) -and ($foreign -or -not (Test-Python $vpy))
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

# ------------------------------------------------------------------ 2b. Git (for Update in the app)
$gitDir = Join-Path $Base 'git'
$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) { Step 'Git' 'OK' $git.Source }
elseif (Test-Path (Join-Path $gitDir 'cmd\git.exe')) { Step 'Git' 'OK' (Join-Path $gitDir 'cmd\git.exe') }
else {
  try {
    $sfx = Get-File $Sources.portable_git.url $Sources.portable_git.sha256 'PortableGit.7z.exe'
    $p = Start-Process -FilePath $sfx -ArgumentList @("-o$gitDir", '-y') -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "extract exit code $($p.ExitCode)" }
    Step 'Git' 'OK' ("Portable Git unpacked to $gitDir")
  } catch { Step 'Git' 'WARN' ("could not unpack Portable Git: $($_.Exception.Message); Update in the app will not work") }
}

# ------------------------------------------------------------------ 3. icon + shortcuts
# the file name carries the setup version: Windows caches shortcut icons by path
$setupVersion = (Get-Content (Join-Path $ScriptDir 'version.txt') -Raw).Trim()
Get-ChildItem $Base -Filter 'app*.ico' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
$icon = Join-Path $Base "app-$setupVersion.ico"
$boot = "import sys, runpy; sys.path.insert(0, r'$Repo'); from powertools.icon import save_ico; save_ico(r'$icon')"
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
