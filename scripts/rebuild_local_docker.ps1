param(
    [Parameter(Mandatory = $true)][string]$EnvFile,
    [int]$Port = 8993,
    [string]$Project = "nexustrader-phase2-ops",
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Arquivo de ambiente local ausente."
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Porta local invalida."
}
if ($Project -notmatch '^[a-z0-9][a-z0-9_-]{0,62}$') {
    throw "Nome de projeto Docker invalido."
}

foreach ($line in Get-Content -LiteralPath $EnvFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
    if ($trimmed -notmatch '^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        throw "Arquivo de ambiente contem uma linha invalida."
    }
    $name = $Matches[1]
    $value = $Matches[2].Trim()
    if (
        $value.Length -ge 2 -and
        (($value.StartsWith('"') -and $value.EndsWith('"')) -or
         ($value.StartsWith("'") -and $value.EndsWith("'")))
    ) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}

$env:ENV_FILE = (Resolve-Path -LiteralPath $EnvFile).Path
$env:HOST_BIND = "127.0.0.1"
$env:HOST_PORT = [string]$Port
$env:ALLOW_REAL_TRADING = "false"
$env:REAL_MAX_STAKE_USD = "0"
$env:DERIV_ACCOUNT_TYPE = "demo"
$env:NEXUS_HUMAN_ACTION_KEY = "local-human-$([guid]::NewGuid().ToString('N'))"
$env:NEXUS_HUMAN_ACTOR = "human:local-validation"

if (
    $env:ALLOW_REAL_TRADING -ne "false" -or
    $env:REAL_MAX_STAKE_USD -ne "0" -or
    $env:DERIV_ACCOUNT_TYPE -ne "demo" -or
    [string]::IsNullOrWhiteSpace($env:NEXUS_HUMAN_ACTION_KEY) -or
    $env:NEXUS_HUMAN_ACTION_KEY -eq $env:DASHBOARD_API_KEY -or
    $env:NEXUS_HUMAN_ACTION_KEY -eq $env:INTERNAL_API_TOKEN -or
    $env:NEXUS_HUMAN_ACTION_KEY -eq $env:DERIV_API_TOKEN
) {
    throw "Overrides locais de seguranca nao foram aplicados."
}

if ($PreflightOnly) {
    [ordered]@{
        outcome = "SAFE_DOCKER_PREFLIGHT"
        bind = "127.0.0.1"
        port = $Port
        account_type = "demo"
        allow_real_trading = $false
        real_max_stake_usd = 0
    } | ConvertTo-Json -Compress
    exit 0
}

& rtk docker compose -p $Project up -d --build
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao reconstruir a stack Docker local."
}

[ordered]@{
    outcome = "LOCAL_DOCKER_REBUILT"
    project = $Project
    bind = "127.0.0.1"
    port = $Port
    account_type = "demo"
    allow_real_trading = $false
    real_max_stake_usd = 0
} | ConvertTo-Json -Compress
