<#
.SYNOPSIS
  Removes the figma2qt plugin installed by install.ps1.
#>
[CmdletBinding()]
param(
    [string]$Target = (Join-Path $env:USERPROFILE '.gemini\config\plugins\figma2qt'),
    [switch]$KeepEnv
)

$ErrorActionPreference = 'Stop'
if (Test-Path $Target) {
    Remove-Item $Target -Recurse -Force
    Write-Host "Removed $Target"
} else {
    Write-Host "Nothing to remove at $Target"
}
if (-not $KeepEnv) {
    [Environment]::SetEnvironmentVariable('FIGMA2QT_HOME', $null, 'User')
    Remove-Item Env:\FIGMA2QT_HOME -ErrorAction SilentlyContinue
    Write-Host 'Cleared FIGMA2QT_HOME'
}
