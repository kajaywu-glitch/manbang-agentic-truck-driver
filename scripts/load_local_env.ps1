param(
    [string]$Path = ""
)

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Path) {
    $Path = Join-Path $repoRoot ".env.local"
}

if (-not (Test-Path -LiteralPath $Path)) {
    throw "Local env file not found: $Path"
}

Get-Content -LiteralPath $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }
    if ($line -notmatch "^\s*([^=]+?)\s*=\s*(.*)\s*$") {
        return
    }

    $name = $matches[1].Trim()
    $value = $matches[2].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}

if (-not $env:TIANCHI_MODEL_API_KEY -and $env:DASHSCOPE_API_KEY) {
    [Environment]::SetEnvironmentVariable("TIANCHI_MODEL_API_KEY", $env:DASHSCOPE_API_KEY, "Process")
}

$hasKey = [bool]($env:DASHSCOPE_API_KEY -or $env:TIANCHI_MODEL_API_KEY)
Write-Host ("Loaded local env from {0}. key_present={1}, qwen={2}" -f $Path, $hasKey, $env:AGENT_ENABLE_QWEN35_FLASH)
