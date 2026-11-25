<#
.SYNOPSIS
  Installs the figma2qt plugin into the Antigravity global plugin directory.

.DESCRIPTION
  Copies plugin/ (plugin.json, skills, agents, rules), engine/, profiles/,
  examples/ and bin/ into the target directory and sets the user environment
  variable FIGMA2QT_HOME. Re-running the script upgrades an existing install.

.PARAMETER Target
  Install directory. Default: %USERPROFILE%\.gemini\config\plugins\figma2qt

.PARAMETER InstallDeps
  Also run: python -m pip install -r engine\requirements.txt

.PARAMETER NoEnv
  Do not set the FIGMA2QT_HOME user environment variable.
#>
[CmdletBinding()]
param(
    [string]$Target = (Join-Path $env:USERPROFILE '.gemini\config\plugins\figma2qt'),
    [switch]$InstallDeps,
    [switch]$NoEnv
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

function Copy-Tree([string]$Source, [string]$Destination) {
    if (-not (Test-Path $Source)) { return }
    New-Item -ItemType Directory -Force $Destination | Out-Null
    robocopy $Source $Destination /E /XD __pycache__ build /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $Source" }
    $global:LASTEXITCODE = 0
}

if (-not (Test-Path (Join-Path $repo 'bin\qt_preview.exe'))) {
    Write-Warning 'bin\qt_preview.exe not found. Build it first: scripts\build_preview.ps1 -QtPrefix <Qt install root>'
}

foreach ($name in 'skills', 'agents', 'rules', 'engine', 'bin', 'profiles', 'examples') {
    $path = Join-Path $Target $name
    if (Test-Path $path) { Remove-Item $path -Recurse -Force }
}
New-Item -ItemType Directory -Force $Target | Out-Null

Copy-Item (Join-Path $repo 'plugin\plugin.json') $Target -Force
foreach ($name in 'skills', 'agents', 'rules') {
    Copy-Tree (Join-Path $repo "plugin\$name") (Join-Path $Target $name)
}
foreach ($name in 'engine', 'profiles', 'examples', 'bin') {
    Copy-Tree (Join-Path $repo $name) (Join-Path $Target $name)
}

if (-not $NoEnv) {
    [Environment]::SetEnvironmentVariable('FIGMA2QT_HOME', $Target, 'User')
    $env:FIGMA2QT_HOME = $Target
}

if ($InstallDeps) {
    python -m pip install -r (Join-Path $Target 'engine\requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
}

Write-Host "figma2qt installed to: $Target"
if (-not $NoEnv) { Write-Host "FIGMA2QT_HOME = $Target (open a new terminal / restart Antigravity to pick it up)" }
Write-Host 'Next, in each project:'
Write-Host "  python `"$Target\engine\orchestrator.py`" init"
Write-Host '  (fill qt.prefix, font.family, font.dir in .figma2qt\profile.yaml)'
Write-Host "  python `"$Target\engine\orchestrator.py`" preflight"
Write-Host "  python `"$Target\engine\orchestrator.py`" selftest"
