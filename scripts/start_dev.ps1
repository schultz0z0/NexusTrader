param(
    [string]$BindAddress = "127.0.0.1",
    [int]$Port = 8990,
    [string]$EnvFile = "",
    [string]$DatabasePath = "",
    [string]$LogsDirectory = "",
    [string]$PidDirectory = "",
    [string]$RunId = "dev",
    [switch]$ApiOnly,
    [switch]$BotOnly,
    [switch]$StopOwned,
    [switch]$Detached,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($BindAddress -notin @("127.0.0.1", "localhost")) {
    throw "O launcher local aceita somente bind loopback."
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "A porta local deve estar entre 1 e 65535."
}
if ($ApiOnly -and $BotOnly) {
    throw "ApiOnly e BotOnly nao podem ser combinados."
}
if ($RunId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
    throw "RunId invalido. Use apenas letras, numeros, ponto, hifen e sublinhado."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $projectRoot ".env"
}
if ([string]::IsNullOrWhiteSpace($DatabasePath)) {
    $defaultDatabaseName = if ($RunId -eq "dev") {
        "nexus_trader.dev.db"
    } else {
        "nexus_trader.$RunId.db"
    }
    $DatabasePath = Join-Path $projectRoot "storage\$defaultDatabaseName"
}
if ([string]::IsNullOrWhiteSpace($LogsDirectory)) {
    $LogsDirectory = Join-Path $projectRoot "logs\$RunId"
}
if ([string]::IsNullOrWhiteSpace($PidDirectory)) {
    $PidDirectory = Join-Path $projectRoot "storage\pids\$RunId"
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Ambiente Python ausente. Crie .venv e instale requirements.txt antes de iniciar."
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Arquivo de ambiente ausente. Informe um arquivo local somente como entrada."
}

function Import-LocalEnvironment {
    param([string]$Path)
    foreach ($line in Get-Content -LiteralPath $Path) {
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
}

Import-LocalEnvironment -Path $EnvFile

$databaseParent = Split-Path -Parent $DatabasePath
if ([string]::IsNullOrWhiteSpace($databaseParent)) {
    $databaseParent = $projectRoot
    $DatabasePath = Join-Path $databaseParent $DatabasePath
}

# Overrides sao deliberadamente aplicados depois do env file e somente neste processo.
$env:ALLOW_REAL_TRADING = "false"
$env:REAL_MAX_STAKE_USD = "0"
$env:DERIV_ACCOUNT_TYPE = "demo"
$env:NEXUS_DEMO_STAKE = "0.35"
$env:DEV_MODE = "true"
$env:DOMAIN = "localhost"
$env:DB_PATH = $DatabasePath
$env:NEXUS_TICK_ARCHIVE_PATH = Join-Path $databaseParent "nexus_ticks.$RunId"
$env:API_BASE_URL = "http://${BindAddress}:${Port}"
$env:NEXUS_HUMAN_ACTION_KEY = "local-human-$([guid]::NewGuid().ToString('N'))"
$env:NEXUS_HUMAN_ACTOR = "local-validation-operator"

if ($env:ALLOW_REAL_TRADING -ne "false" -or $env:DERIV_ACCOUNT_TYPE -ne "demo") {
    throw "Overrides de seguranca local nao foram aplicados."
}
if (
    $env:NEXUS_HUMAN_ACTION_KEY -eq $env:DASHBOARD_API_KEY -or
    $env:NEXUS_HUMAN_ACTION_KEY -eq $env:INTERNAL_API_TOKEN -or
    $env:NEXUS_HUMAN_ACTION_KEY -eq $env:DERIV_API_TOKEN
) {
    throw "A credencial humana local deve ser exclusiva."
}

if ($PreflightOnly) {
    [ordered]@{
        outcome = "SAFE_PREFLIGHT"
        bind = "loopback"
        port = $Port
        account_type = "demo"
        allow_real_trading = $false
        real_max_stake_usd = 0
        nexus_demo_stake = 0.35
        human_action_key_distinct = $true
        api_only = [bool]$ApiOnly
        bot_only = [bool]$BotOnly
    } | ConvertTo-Json -Compress
    return
}

New-Item -ItemType Directory -Force -Path $databaseParent, $LogsDirectory, $PidDirectory | Out-Null

$apiStdout = Join-Path $LogsDirectory "api.stdout.log"
$apiStderr = Join-Path $LogsDirectory "api.stderr.log"
$botStdout = Join-Path $LogsDirectory "bot.stdout.log"
$botStderr = Join-Path $LogsDirectory "bot.stderr.log"
$apiPidFile = Join-Path $PidDirectory "api.pid"
$botPidFile = Join-Path $PidDirectory "bot.pid"
$apiWorkerPidFile = Join-Path $PidDirectory "api.worker.pid"
$botWorkerPidFile = Join-Path $PidDirectory "bot.worker.pid"
$healthUrl = "http://${BindAddress}:${Port}/api/v1/health/live"

function Test-PortInUse {
    param([int]$LocalPort)
    return [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners().Port -contains $LocalPort
}

function Wait-ForApi {
    param([System.Diagnostics.Process]$OwnedApi)
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($null -ne $OwnedApi -and $OwnedApi.HasExited) {
            throw "A API encerrou durante a inicializacao. Consulte o log isolado."
        }
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
            if ($health.status -eq "alive") { return }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    throw "A API local nao ficou saudavel no prazo. Consulte o log isolado."
}

function Get-VerifiedDescendants {
    param([int]$RootId)
    $frontier = @($RootId)
    $descendants = @()
    while ($frontier.Count -gt 0) {
        $next = @()
        foreach ($parentId in $frontier) {
            $children = @(
                Get-CimInstance Win32_Process |
                    Where-Object { $_.ParentProcessId -eq $parentId }
            )
            foreach ($child in $children) {
                $descendants += [int]$child.ProcessId
                $next += [int]$child.ProcessId
            }
        }
        $frontier = $next
    }
    return $descendants
}

function Test-OwnedDescendant {
    param([int]$RootId, [int]$CandidateId)
    $currentId = $CandidateId
    for ($depth = 0; $depth -lt 32 -and $currentId -gt 0; $depth++) {
        if ($currentId -eq $RootId) { return $true }
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$currentId"
        if ($null -eq $current) { return $false }
        $currentId = [int]$current.ParentProcessId
    }
    return $false
}

function Find-OwnedWorker {
    param(
        [System.Diagnostics.Process]$Root,
        [string]$CommandPattern,
        [int]$ListenerPort = 0
    )
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        $candidateIds = if ($ListenerPort -gt 0) {
            @(
                Get-NetTCPConnection -LocalPort $ListenerPort -State Listen -ErrorAction SilentlyContinue |
                    Select-Object -ExpandProperty OwningProcess
            )
        } else {
            @(Get-VerifiedDescendants -RootId $Root.Id)
        }
        foreach ($candidateId in $candidateIds) {
            $candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$candidateId"
            if (
                $null -ne $candidate -and
                (Test-OwnedDescendant -RootId $Root.Id -CandidateId $candidateId) -and
                $candidate.CommandLine -match $CommandPattern
            ) {
                return [int]$candidateId
            }
        }
        Start-Sleep -Milliseconds 100
    }
    throw "Worker filho do launcher nao foi comprovado no prazo."
}

function Stop-OwnedProcess {
    param(
        [System.Diagnostics.Process]$Process,
        [int]$WorkerId = 0,
        [string]$WorkerPattern = "python"
    )
    if ($null -eq $Process) { return }
    $rootId = $Process.Id
    if ($WorkerId -gt 0) {
        $worker = Get-CimInstance Win32_Process -Filter "ProcessId=$WorkerId"
        if (
            $null -eq $worker -or
            -not (Test-OwnedDescendant -RootId $rootId -CandidateId $WorkerId) -or
            $worker.CommandLine -notmatch $WorkerPattern
        ) {
            throw "Recusando encerrar worker sem ownership comprovado."
        }
        Stop-Process -Id $WorkerId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 100
    }
    $descendants = @(Get-VerifiedDescendants -RootId $rootId)
    for ($index = $descendants.Count - 1; $index -ge 0; $index--) {
        Stop-Process -Id $descendants[$index] -Force -ErrorAction SilentlyContinue
    }
    $current = Get-Process -Id $rootId -ErrorAction SilentlyContinue
    if ($null -ne $current) {
        Stop-Process -Id $rootId -ErrorAction SilentlyContinue
        $current.WaitForExit(5000) | Out-Null
    }
}

function Stop-OwnedRecord {
    param(
        [string]$RootPidFile,
        [string]$WorkerPidFile,
        [string]$RootPattern,
        [string]$WorkerPattern
    )
    if (-not (Test-Path -LiteralPath $RootPidFile -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath $WorkerPidFile -PathType Leaf)) {
        throw "PID do worker ausente; cleanup recusado."
    }
    $rootId = [int](Get-Content -LiteralPath $RootPidFile)
    $workerId = [int](Get-Content -LiteralPath $WorkerPidFile)
    $rootCim = Get-CimInstance Win32_Process -Filter "ProcessId=$rootId"
    if ($null -eq $rootCim) {
        Remove-Item -LiteralPath $RootPidFile, $WorkerPidFile -Force
        return $true
    }
    if ($rootCim.CommandLine -notmatch $RootPattern) {
        throw "PID root nao corresponde ao processo registrado; cleanup recusado."
    }
    $root = Get-Process -Id $rootId
    Stop-OwnedProcess -Process $root -WorkerId $workerId -WorkerPattern $WorkerPattern
    Remove-Item -LiteralPath $RootPidFile, $WorkerPidFile -Force
    return $true
}

if ($StopOwned) {
    $botStopped = Stop-OwnedRecord -RootPidFile $botPidFile -WorkerPidFile $botWorkerPidFile `
        -RootPattern "main.py" -WorkerPattern "main.py"
    $apiStopped = Stop-OwnedRecord -RootPidFile $apiPidFile -WorkerPidFile $apiWorkerPidFile `
        -RootPattern "uvicorn api.app:app" -WorkerPattern "uvicorn api.app:app"
    [ordered]@{
        outcome = "STOPPED_OWNED"
        api_stopped = [bool]$apiStopped
        bot_stopped = [bool]$botStopped
        port_closed = -not (Test-PortInUse -LocalPort $Port)
    } | ConvertTo-Json -Compress
    return
}

if (-not $BotOnly -and (Test-PortInUse -LocalPort $Port)) {
    throw "A porta local solicitada ja esta em uso; nenhum processo foi alterado."
}

$apiProcess = $null
$botProcess = $null
$apiWorkerId = 0
$botWorkerId = 0

try {
    if (-not $BotOnly) {
        $apiProcess = Start-Process -FilePath $python `
            -ArgumentList @("-m", "uvicorn", "api.app:app", "--host", $BindAddress, "--port", $Port, "--no-access-log", "--log-level", "warning") `
            -WorkingDirectory $projectRoot `
            -RedirectStandardOutput $apiStdout `
            -RedirectStandardError $apiStderr `
            -WindowStyle Hidden `
            -PassThru
        Wait-ForApi -OwnedApi $apiProcess
        Set-Content -LiteralPath $apiPidFile -Value $apiProcess.Id
        $apiWorkerId = Find-OwnedWorker -Root $apiProcess `
            -CommandPattern "uvicorn api.app:app" -ListenerPort $Port
        Set-Content -LiteralPath $apiWorkerPidFile -Value $apiWorkerId
    } else {
        Wait-ForApi -OwnedApi $null
    }

    if (-not $ApiOnly) {
        $botProcess = Start-Process -FilePath $python `
            -ArgumentList @("main.py") `
            -WorkingDirectory $projectRoot `
            -RedirectStandardOutput $botStdout `
            -RedirectStandardError $botStderr `
            -WindowStyle Hidden `
            -PassThru
        Set-Content -LiteralPath $botPidFile -Value $botProcess.Id
        $botWorkerId = Find-OwnedWorker -Root $botProcess -CommandPattern "main.py"
        Set-Content -LiteralPath $botWorkerPidFile -Value $botWorkerId
    }

    [ordered]@{
        outcome = "STARTED_SAFE"
        url = "http://${BindAddress}:${Port}"
        api_pid = if ($null -eq $apiProcess) { $null } else { $apiProcess.Id }
        api_worker_pid = if ($apiWorkerId -eq 0) { $null } else { $apiWorkerId }
        bot_pid = if ($null -eq $botProcess) { $null } else { $botProcess.Id }
        bot_worker_pid = if ($botWorkerId -eq 0) { $null } else { $botWorkerId }
        account_type = "demo"
        allow_real_trading = $false
    } | ConvertTo-Json -Compress

    if ($Detached) { return }

    Write-Host "Pressione Ctrl+C para encerrar apenas os processos iniciados por este launcher."
    while (
        ($null -eq $apiProcess -or -not $apiProcess.HasExited) -and
        ($null -eq $botProcess -or -not $botProcess.HasExited)
    ) {
        Start-Sleep -Seconds 1
    }
    if ($null -ne $apiProcess -and $apiProcess.HasExited) {
        throw "A API encerrou inesperadamente."
    }
    if ($null -ne $botProcess -and $botProcess.HasExited) {
        throw "O bot encerrou inesperadamente."
    }
} finally {
    if (-not $Detached) {
        Stop-OwnedProcess -Process $botProcess -WorkerId $botWorkerId -WorkerPattern "main.py"
        Stop-OwnedProcess -Process $apiProcess -WorkerId $apiWorkerId -WorkerPattern "uvicorn api.app:app"
    }
}
