[CmdletBinding()]
param(
    [ValidateSet("agentops", "agentops-worker")]
    [string]$Tool = "agentops",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ToolArguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$installRoot = $env:AGENTOPS_WINDOWS_INSTALL_ROOT
if ([string]::IsNullOrWhiteSpace($installRoot)) {
    $installRoot = Join-Path $env:LOCALAPPDATA "AgentOps MIS\cli"
}
$installRoot = [System.IO.Path]::GetFullPath($installRoot)
$currentPath = Join-Path $installRoot "current.json"
if (-not (Test-Path -LiteralPath $currentPath -PathType Leaf)) {
    throw "AgentOps MIS CLI is not installed"
}
if (((Get-Item -LiteralPath $currentPath -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "unsafe AgentOps MIS current manifest"
}
$current = Get-Content -LiteralPath $currentPath -Raw | ConvertFrom-Json
if ($current.schema_version -ne 1 -or $current.product -ne "AgentOps MIS Windows CLI" -or
    [string]$current.version -notmatch '^[A-Za-z0-9._-]+$') {
    throw "invalid AgentOps MIS current manifest"
}
$executable = Join-Path $installRoot ("versions\" + [string]$current.version + "\venv\Scripts\" + $Tool + ".exe")
$resolvedExecutable = [System.IO.Path]::GetFullPath($executable)
$versionsRoot = [System.IO.Path]::GetFullPath((Join-Path $installRoot "versions"))
if (-not $resolvedExecutable.StartsWith($versionsRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath $resolvedExecutable -PathType Leaf)) {
    throw "installed AgentOps MIS entry point is missing or unsafe"
}
& $resolvedExecutable @ToolArguments
exit $LASTEXITCODE
