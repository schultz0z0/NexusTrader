param(
    [string]$BindAddress = "127.0.0.1",
    [int]$Port = 8990,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"
$storageDir = Join-Path $projectRoot "storage"
$logsDir = Join-Path $projectRoot "logs"
$databasePath = Join-Path $storageDir "nexus_trader.dev.db"
$apiStdout = Join-Path $logsDir "dev-api.stdout.log"
$apiStderr = Join-Path $logsDir "dev-api.stderr.log"
$botStdout = Join-Path $logsDir "dev-bot.stdout.log"
$botStderr = Join-Path $logsDir "dev-bot.stderr.log"
$healthUrl = "http://${BindAddress}:${Port}/api/v1/health"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Ambiente Python ausente. Crie .venv e instale requirements.txt antes de iniciar."
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Arquivo .env ausente. Copie .env.example e informe somente credenciais DEMO."
}

New-Item -ItemType Directory -Force -Path $storageDir, $logsDir | Out-Null

# Overrides locais são deliberadamente aplicados somente a estes processos.
$env:ALLOW_REAL_TRADING = "false"
$env:DERIV_ACCOUNT_TYPE = "demo"
$env:DEV_MODE = "true"
$env:DOMAIN = "localhost"
$env:DB_PATH = $databasePath
$env:API_BASE_URL = "http://${BindAddress}:${Port}"

$apiProcess = $null
$botProcess = $null

function Stop-OwnedProcess {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process) { return }
    $current = Get-Process -Id $Process.Id -ErrorAction SilentlyContinue
    if ($null -ne $current) {
        Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
        $current.WaitForExit(5000) | Out-Null
    }
}

try {
    $apiProcess = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "api.app:app", "--host", $BindAddress, "--port", $Port, "--no-access-log") `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $apiStdout `
        -RedirectStandardError $apiStderr `
        -WindowStyle Hidden `
        -PassThru

    $healthy = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($apiProcess.HasExited) {
            throw "A API encerrou durante a inicialização. Consulte $apiStderr"
        }
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
            if ($health.status -eq "ok") {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $healthy) {
        throw "A API não ficou saudável em $healthUrl. Consulte $apiStderr"
    }

    $botProcess = Start-Process -FilePath $python `
        -ArgumentList @("main.py") `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $botStdout `
        -RedirectStandardError $botStderr `
        -WindowStyle Hidden `
        -PassThru

    Set-Content -LiteralPath (Join-Path $storageDir "dev-api.pid") -Value $apiProcess.Id
    Set-Content -LiteralPath (Join-Path $storageDir "dev-bot.pid") -Value $botProcess.Id

    Write-Host "NexusTrader local iniciado somente em DEMO: http://${BindAddress}:${Port}"
    Write-Host "API PID $($apiProcess.Id) | log: $apiStderr"
    Write-Host "Bot PID $($botProcess.Id) | log: $botStderr"

    if ($Detached) {
        return
    }

    Write-Host "Pressione Ctrl+C para encerrar apenas estes dois processos."
    while (-not $apiProcess.HasExited -and -not $botProcess.HasExited) {
        Start-Sleep -Seconds 1
    }
    if ($apiProcess.HasExited) { throw "A API encerrou inesperadamente." }
    if ($botProcess.HasExited) { throw "O bot encerrou inesperadamente." }
} finally {
    if (-not $Detached) {
        Stop-OwnedProcess -Process $botProcess
        Stop-OwnedProcess -Process $apiProcess
    }
}
