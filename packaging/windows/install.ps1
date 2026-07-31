[CmdletBinding()]
param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot "..\.."),
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "AgentOps MIS\cli"),
    [string]$BinDir = (Join-Path $env:LOCALAPPDATA "AgentOps MIS\bin"),
    [switch]$AddToPath,
    [switch]$TestMode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Product = "AgentOps MIS Windows CLI"
$SchemaVersion = 1
$ManagedFiles = @(
    "agentops.cmd",
    "agentops-worker.cmd",
    "agentops-launcher.ps1",
    "managed.json"
)

function Write-JsonResult {
    param([hashtable]$Payload)
    $Payload["credentials_read"] = $false
    $Payload["token_omitted"] = $true
    $Payload["database_content_read"] = $false
    $Payload | ConvertTo-Json -Depth 6 -Compress
}

function Resolve-SafePath {
    param(
        [string]$Value,
        [string]$Label
    )
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.IndexOfAny([char[]]"`r`n`0`"") -ge 0) {
        throw "$Label is invalid"
    }
    $resolved = [System.IO.Path]::GetFullPath($Value)
    $root = [System.IO.Path]::GetPathRoot($resolved)
    if ($resolved -eq $root) {
        throw "$Label may not be a filesystem root"
    }
    return $resolved.TrimEnd([System.IO.Path]::DirectorySeparatorChar)
}

function Assert-NoReparsePoint {
    param([string]$PathValue)
    $candidate = $PathValue
    while (-not [string]::IsNullOrEmpty($candidate)) {
        if (Test-Path -LiteralPath $candidate) {
            $item = Get-Item -LiteralPath $candidate -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "managed path contains a reparse point"
            }
        }
        $parent = Split-Path -Parent $candidate
        if ($parent -eq $candidate) {
            break
        }
        $candidate = $parent
    }
}

function Find-Python {
    $candidates = @()
    $py = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        $candidates += ,@($py.Source, "-3")
    }
    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        $candidates += ,@($python.Source)
    }
    foreach ($candidate in $candidates) {
        $executable = $candidate[0]
        $prefix = @()
        if ($candidate.Count -gt 1) {
            $prefix = $candidate[1..($candidate.Count - 1)]
        }
        $versionJson = & $executable @prefix -c "import json,sys; print(json.dumps({'ok': sys.version_info >= (3,10), 'version': '.'.join(map(str, sys.version_info[:3]))}))"
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        $version = $versionJson | ConvertFrom-Json
        if ($version.ok -eq $true) {
            return @{
                executable = $executable
                prefix = $prefix
                version = [string]$version.version
            }
        }
    }
    throw "Python 3.10 or newer is required"
}

function Invoke-Checked {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$Failure
    )
    & $Executable @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw $Failure
    }
}

function Write-AtomicJson {
    param(
        [string]$PathValue,
        [hashtable]$Payload
    )
    $temporary = "$PathValue.$([Guid]::NewGuid().ToString('N')).tmp"
    $json = $Payload | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText($temporary, $json + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $PathValue -Force
}

function Write-CmdShim {
    param(
        [string]$PathValue,
        [string]$Executable
    )
    if ($Executable.Contains('"')) {
        throw "unsafe executable path"
    }
    $body = "@echo off`r`n`"$Executable`" %*`r`nexit /b %ERRORLEVEL%`r`n"
    [System.IO.File]::WriteAllText($PathValue, $body, (New-Object System.Text.ASCIIEncoding))
}

function Add-UserPathEntry {
    param([string]$PathValue)
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        $parts = @($current.Split(';') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    }
    $exists = $false
    foreach ($part in $parts) {
        if ($part.TrimEnd('\') -ieq $PathValue.TrimEnd('\')) {
            $exists = $true
            break
        }
    }
    if (-not $exists) {
        $next = (@($parts) + @($PathValue)) -join ';'
        [Environment]::SetEnvironmentVariable("Path", $next, "User")
    }
    if (-not (($env:Path -split ';') -contains $PathValue)) {
        $env:Path = "$PathValue;$env:Path"
    }
    return (-not $exists)
}

try {
    if (-not $TestMode -and [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "this installer supports Windows only"
    }

    $source = Resolve-SafePath -Value $SourceRoot -Label "SourceRoot"
    $install = Resolve-SafePath -Value $InstallRoot -Label "InstallRoot"
    $bin = Resolve-SafePath -Value $BinDir -Label "BinDir"
    if (-not (Test-Path -LiteralPath (Join-Path $source "pyproject.toml") -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $source "agentops_mis_cli\_build_backend.py") -PathType Leaf)) {
        throw "SourceRoot is not an AgentOps MIS source tree"
    }
    Assert-NoReparsePoint -PathValue $install
    Assert-NoReparsePoint -PathValue $bin

    $python = Find-Python
    $temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("agentops-windows-install-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        $buildCode = @'
import json
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
from agentops_mis_cli._build_backend import build_wheel
name = build_wheel(sys.argv[2])
print(json.dumps({"wheel": name}))
'@
        $buildArgs = @($python.prefix) + @("-c", $buildCode, $source, $temporary)
        $wheelJson = & $python.executable @buildArgs
        if ($LASTEXITCODE -ne 0) {
            throw "offline wheel build failed"
        }
        $wheelName = [string](($wheelJson | ConvertFrom-Json).wheel)
        $wheelPath = Join-Path $temporary $wheelName
        if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) {
            throw "offline wheel was not created"
        }
        $inspectCode = @'
import hashlib
import json
import pathlib
import sys
import zipfile
p = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(p) as z:
    metadata_name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
    metadata = z.read(metadata_name).decode("utf-8")
version = next(line.split(":", 1)[1].strip() for line in metadata.splitlines() if line.startswith("Version:"))
print(json.dumps({"version": version, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}))
'@
        $inspectArgs = @($python.prefix) + @("-c", $inspectCode, $wheelPath)
        $wheelInfo = (& $python.executable @inspectArgs) | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$wheelInfo.version)) {
            throw "wheel metadata validation failed"
        }
        $version = [string]$wheelInfo.version
        if ($version -notmatch '^[A-Za-z0-9._-]+$') {
            throw "wheel version is invalid"
        }

        New-Item -ItemType Directory -Path $install -Force | Out-Null
        New-Item -ItemType Directory -Path $bin -Force | Out-Null
        $versionsDir = Join-Path $install "versions"
        New-Item -ItemType Directory -Path $versionsDir -Force | Out-Null
        $target = Join-Path $versionsDir $version
        $reused = $false
        if (Test-Path -LiteralPath $target) {
            Assert-NoReparsePoint -PathValue $target
            $existingManifest = Join-Path $target "install.json"
            if (-not (Test-Path -LiteralPath $existingManifest -PathType Leaf)) {
                throw "existing version is not a managed AgentOps MIS install"
            }
            $existing = Get-Content -LiteralPath $existingManifest -Raw | ConvertFrom-Json
            if ([string]$existing.wheel_sha256 -ne [string]$wheelInfo.sha256) {
                throw "installed version collision: wheel hash differs"
            }
            $reused = $true
        } else {
            New-Item -ItemType Directory -Path $target | Out-Null
            try {
                $venv = Join-Path $target "venv"
                $venvArgs = @($python.prefix) + @("-m", "venv", $venv)
                Invoke-Checked -Executable $python.executable -Arguments $venvArgs -Failure "Python venv creation failed"
                $venvPython = Join-Path $venv "Scripts\python.exe"
                Invoke-Checked -Executable $venvPython -Arguments @("-m", "pip", "install", "--no-index", "--no-deps", $wheelPath) -Failure "offline wheel installation failed"
                $agentopsExe = Join-Path $venv "Scripts\agentops.exe"
                $workerExe = Join-Path $venv "Scripts\agentops-worker.exe"
                Invoke-Checked -Executable $agentopsExe -Arguments @("--help") -Failure "agentops entry point verification failed"
                Invoke-Checked -Executable $workerExe -Arguments @("--help") -Failure "agentops-worker entry point verification failed"
                Write-AtomicJson -PathValue (Join-Path $target "install.json") -Payload @{
                    schema_version = $SchemaVersion
                    product = $Product
                    version = $version
                    wheel_sha256 = [string]$wheelInfo.sha256
                    python_version = [string]$python.version
                    credentials_stored = $false
                }
            } catch {
                if (Test-Path -LiteralPath $target) {
                    Remove-Item -LiteralPath $target -Recurse -Force
                }
                throw
            }
        }

        $activeVenv = Join-Path $target "venv"
        $activeAgentOps = Join-Path $activeVenv "Scripts\agentops.exe"
        $activeWorker = Join-Path $activeVenv "Scripts\agentops-worker.exe"
        Invoke-Checked -Executable $activeAgentOps -Arguments @("--help") -Failure "active agentops entry point verification failed"
        Invoke-Checked -Executable $activeWorker -Arguments @("--help") -Failure "active agentops-worker entry point verification failed"
        $current = Join-Path $install "current.json"
        Write-AtomicJson -PathValue $current -Payload @{
            schema_version = $SchemaVersion
            product = $Product
            version = $version
            wheel_sha256 = [string]$wheelInfo.sha256
        }
        Write-CmdShim -PathValue (Join-Path $bin "agentops.cmd") -Executable $activeAgentOps
        Write-CmdShim -PathValue (Join-Path $bin "agentops-worker.cmd") -Executable $activeWorker
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "launcher.ps1") -Destination (Join-Path $bin "agentops-launcher.ps1") -Force
        $pathAdded = $false
        if ($AddToPath) {
            $pathAdded = Add-UserPathEntry -PathValue $bin
        }
        Write-AtomicJson -PathValue (Join-Path $bin "managed.json") -Payload @{
            schema_version = $SchemaVersion
            product = $Product
            install_root = $install
            bin_dir = $bin
            managed_files = $ManagedFiles
            path_added_by_installer = $pathAdded
        }
        Write-JsonResult -Payload @{
            ok = $true
            operation = "windows_cli_install"
            version = $version
            python_version = [string]$python.version
            wheel_sha256 = [string]$wheelInfo.sha256
            install_root = $install
            bin_dir = $bin
            path_requested = [bool]$AddToPath
            path_added = $pathAdded
            reused_version = $reused
            network_used = $false
            credentials_stored = $false
            next_command = "agentops --help"
        }
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Recurse -Force
        }
    }
} catch {
    Write-JsonResult -Payload @{
        ok = $false
        operation = "windows_cli_install"
        error = $_.Exception.Message
        network_used = $false
        credentials_stored = $false
    }
    exit 1
}
