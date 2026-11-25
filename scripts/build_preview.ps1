<#
.SYNOPSIS
  Builds the qt_preview renderer and copies it to bin\qt_preview.exe.

.PARAMETER QtPrefix
  Qt install root, e.g. C:\Qt\5.15.2\msvc2019_64 (must match the Qt you will
  put in each project's profile qt.prefix).

.PARAMETER Generator
  CMake generator. Default: Visual Studio 16 2019 (use the one matching your Qt).
.PARAMETER BuildDir
  CMake build tree. Default: <repo>\build\qt_preview. On machines with transparent
  source-code encryption, pick a folder outside the protected area if the
  configure step fails on *.cpp.dse.tmp files (for example
  %LOCALAPPDATA%\figma2qt\build).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$QtPrefix,
    [string]$Generator = 'Visual Studio 16 2019',
    [string]$Config = 'Release',
    [string]$BuildDir = ''
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$source = Join-Path $repo 'engine\qt_preview'
$build = if ($BuildDir) { $BuildDir } else { Join-Path $repo 'build\qt_preview' }

cmake -S $source -B $build -G $Generator -A x64 "-DCMAKE_PREFIX_PATH=$QtPrefix"
if ($LASTEXITCODE -ne 0) { throw 'cmake configure failed' }
cmake --build $build --config $Config
if ($LASTEXITCODE -ne 0) { throw 'cmake build failed' }

$exe = Join-Path $build "$Config\qt_preview.exe"
if (-not (Test-Path $exe)) { throw "build output not found: $exe" }
New-Item -ItemType Directory -Force (Join-Path $repo 'bin') | Out-Null
Copy-Item $exe (Join-Path $repo 'bin\qt_preview.exe') -Force
Write-Host "bin\qt_preview.exe updated ($QtPrefix)"
