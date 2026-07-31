[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "AgentOps MIS\cli"),
    [string]$BinDir = (Join-Path $env:LOCALAPPDATA "AgentOps MIS\bin"),
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA "AgentOps MIS"),
    [switch]$PurgeData,
    [switch]$ConfirmPurgeData,
    [switch]$KeepPath,
    [switch]$TestMode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Product = "AgentOps MIS Windows CLI"

function Write-JsonResult {
    param([hashtable]$Payload)
    $Payload["credentials_read"] = $false
    $Payload["token_omitted"] = $true
    $Payload["database_content_read"] = $false
    $Payload | ConvertTo-Json -Depth 5 -Compress
}

function Resolve-SafePath {
    param([string]$Value, [string]$Label)
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.IndexOfAny([char[]]"`r`n`0`"") -ge 0) {
        throw "$Label is invalid"
    }
    $resolved = [System.IO.Path]::GetFullPath($Value)
    if ($resolved -eq [System.IO.Path]::GetPathRoot($resolved)) {
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
        if ($parent -eq $candidate) { break }
        $candidate = $parent
    }
}

function Remove-UserPathEntry {
    param([string]$PathValue)
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrWhiteSpace($current)) { return $false }
    $parts = @($current.Split(';') | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_) -and $_.TrimEnd('\') -ine $PathValue.TrimEnd('\')
    })
    $next = $parts -join ';'
    if ($next -eq $current) { return $false }
    [Environment]::SetEnvironmentVariable("Path", $next, "User")
    return $true
}

function Get-ManagedWorkerTaskCount {
    param([string]$InstallPath)
    $scheduledTask = Get-Command "Get-ScheduledTask" -ErrorAction SilentlyContinue
    if ($null -eq $scheduledTask) {
        throw "Task Scheduler inspection is unavailable; refusing uninstall"
    }
    $prefix = $InstallPath.TrimEnd('\') + "\versions\"
    $managed = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {
        $task = $_
        if ($task.TaskName -like "local.agentops.worker.*") { return $true }
        foreach ($action in @($task.Actions)) {
            $execute = if ($action.PSObject.Properties.Name -contains "Execute") { [string]$action.Execute } else { "" }
            $arguments = if ($action.PSObject.Properties.Name -contains "Arguments") { [string]$action.Arguments } else { "" }
            if ($execute.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -or
                $arguments.Contains("agentops_mis_cli.worker")) {
                return $true
            }
        }
        return $false
    })
    return $managed.Count
}

try {
    if (-not $TestMode -and [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "this uninstaller supports Windows only"
    }
    if ($PurgeData -and -not $ConfirmPurgeData) {
        throw "-PurgeData requires -ConfirmPurgeData"
    }
    $install = Resolve-SafePath -Value $InstallRoot -Label "InstallRoot"
    $bin = Resolve-SafePath -Value $BinDir -Label "BinDir"
    $data = Resolve-SafePath -Value $DataRoot -Label "DataRoot"
    Assert-NoReparsePoint -PathValue $install
    Assert-NoReparsePoint -PathValue $bin
    if ($PurgeData) { Assert-NoReparsePoint -PathValue $data }

    $markerPath = Join-Path $bin "managed.json"
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
        throw "managed Windows CLI marker is missing; refusing broad deletion"
    }
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ($marker.schema_version -ne 1 -or $marker.product -ne $Product -or
        [System.IO.Path]::GetFullPath([string]$marker.install_root) -ine $install -or
        [System.IO.Path]::GetFullPath([string]$marker.bin_dir) -ine $bin) {
        throw "managed Windows CLI marker is invalid"
    }
    $managedTaskCount = Get-ManagedWorkerTaskCount -InstallPath $install
    if ($managedTaskCount -gt 0) {
        throw "managed scheduled Workers remain; unload them with agentops-worker service-control before uninstall"
    }

    $allowed = @("agentops.cmd", "agentops-worker.cmd", "agentops-launcher.ps1", "managed.json")
    foreach ($name in $allowed) {
        $path = Join-Path $bin $name
        if (Test-Path -LiteralPath $path) {
            if (((Get-Item -LiteralPath $path -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "managed launcher is a reparse point"
            }
            Remove-Item -LiteralPath $path -Force
        }
    }
    if (Test-Path -LiteralPath $install) {
        Remove-Item -LiteralPath $install -Recurse -Force
    }
    if ((Test-Path -LiteralPath $bin) -and -not (Get-ChildItem -LiteralPath $bin -Force | Select-Object -First 1)) {
        Remove-Item -LiteralPath $bin -Force
    }
    $pathRemoved = $false
    $pathWasManaged = $marker.PSObject.Properties.Name -contains "path_added_by_installer" -and $marker.path_added_by_installer -eq $true
    if (-not $KeepPath -and $pathWasManaged) {
        $pathRemoved = Remove-UserPathEntry -PathValue $bin
    }
    $dataPurged = $false
    if ($PurgeData -and (Test-Path -LiteralPath $data)) {
        $profile = [System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
        if (-not $data.StartsWith($profile + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "DataRoot must remain below USERPROFILE"
        }
        Remove-Item -LiteralPath $data -Recurse -Force
        $dataPurged = $true
    }
    Write-JsonResult -Payload @{
        ok = $true
        operation = "windows_cli_uninstall"
        install_removed = $true
        path_removed = $pathRemoved
        data_preserved = (-not $dataPurged)
        data_purged = $dataPurged
        managed_worker_tasks = 0
    }
} catch {
    Write-JsonResult -Payload @{
        ok = $false
        operation = "windows_cli_uninstall"
        error = $_.Exception.Message
    }
    exit 1
}
