param(
    [ValidateRange(1, 65535)][int]$BackendPort = 8000,
    [ValidateRange(1, 65535)][int]$FrontendPort = 5173,
    [string]$BackendUrl = '',
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$stateDirectory = Join-Path $projectRoot '.cache\dev-servers'
$stateFile = Join-Path $stateDirectory 'processes.json'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$backendScript = Join-Path $projectRoot 'run.py'
$frontendScript = Join-Path $PSScriptRoot 'frontend-server.mjs'
New-Item -ItemType Directory -Force -Path $stateDirectory | Out-Null

function Stop-ManagedServer($entry) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($entry.pid)"
    if (-not $process) { return }
    # PID reuse must never cause an unrelated process to be stopped.
    if ($process.CreationDate.ToUniversalTime().ToString('o') -ne $entry.created -or
        -not $process.CommandLine.Contains($entry.script)) {
        throw "PID $($entry.pid) no longer matches the saved server. Refusing to stop it."
    }
    # Include the Python ingestion worker. Capture the tree before stopping parents.
    $all = @(Get-CimInstance Win32_Process)
    $descendants = [System.Collections.Generic.List[object]]::new()
    $pending = [System.Collections.Generic.Queue[uint32]]::new()
    $pending.Enqueue([uint32]$entry.pid)
    while ($pending.Count -gt 0) {
        $parentId = $pending.Dequeue()
        foreach ($child in $all) {
            if ($child.ParentProcessId -eq $parentId -and $child.CreationDate -ge $process.CreationDate) {
                $descendants.Add($child)
                $pending.Enqueue($child.ProcessId)
            }
        }
    }
    Stop-Process -Id $entry.pid -Force -ErrorAction SilentlyContinue
    foreach ($child in $descendants) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($child.ProcessId)"
        if ($current -and $current.CreationDate -eq $child.CreationDate) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    Wait-Process -Id $entry.pid -Timeout 15 -ErrorAction SilentlyContinue
}

function Save-Servers($entries) {
    ConvertTo-Json -InputObject @($entries) | Set-Content -LiteralPath $stateFile -Encoding UTF8
}

function Start-Server($executable, $arguments, $name, $script) {
    $process = Start-Process -FilePath $executable -ArgumentList $arguments `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $stateDirectory "$name.log") `
        -RedirectStandardError (Join-Path $stateDirectory "$name.error.log")
    try {
        $info = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)"
        if (-not $info) { throw "$name exited during startup. Check $stateDirectory\$name.error.log" }
    } catch {
        if (-not $process.HasExited) { $process.Kill() }
        throw
    }
    return [pscustomobject]@{ pid = $process.Id; created = $info.CreationDate.ToUniversalTime().ToString('o'); script = $script; name = $name }
}

function Wait-Healthy($url, $entry) {
    $deadline = (Get-Date).AddSeconds(45)
    do {
        if (-not (Get-Process -Id $entry.pid -ErrorAction SilentlyContinue)) {
            throw "$($entry.name) exited. Check $stateDirectory\$($entry.name).error.log"
        }
        try {
            $result = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($result.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    throw "$($entry.name) did not become healthy at $url. Check logs in $stateDirectory"
}

# Serialize restart/stop commands so concurrent invocations cannot orphan servers.
$lockStream = $null
$started = @()
try {
    $lockStream = [System.IO.File]::Open((Join-Path $stateDirectory 'launcher.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    if (-not $Stop) {
        if (-not $BackendUrl) {
            $BackendUrl = $env:FINWHIZ_BACKEND_URL
            $environmentFile = Join-Path $projectRoot '.env'
            if (-not $BackendUrl -and (Test-Path -LiteralPath $environmentFile)) {
                foreach ($line in Get-Content -LiteralPath $environmentFile) {
                    if ($line -match '^\s*(?:export\s+)?FINWHIZ_BACKEND_URL\s*=\s*(.*)$') {
                        $BackendUrl = ($Matches[1] -replace '\s+#.*$', '').Trim().Trim([char[]]@('"', "'"))
                    }
                }
            }
        }
        $useSharedBackend = [bool]$BackendUrl
        if (-not $BackendUrl) {
            if ($BackendPort -eq $FrontendPort) { throw 'Backend and frontend must use different ports.' }
            if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Missing .venv\Scripts\python.exe. Follow docs/LOCAL_DEVELOPMENT.md to install Python dependencies.' }
        } else {
            $backendOrigin = [Uri]$BackendUrl
            if (-not $backendOrigin.IsAbsoluteUri -or $backendOrigin.Scheme -notin @('http', 'https') -or $backendOrigin.AbsolutePath -ne '/' -or $backendOrigin.UserInfo -or $backendOrigin.Query -or $backendOrigin.Fragment) {
                throw 'BackendUrl must be an HTTP(S) origin without credentials or a path.'
            }
        }
        $nodePath = (Get-Command node.exe -ErrorAction Stop).Source
    }
    if (Test-Path -LiteralPath $stateFile) {
        $savedServers = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
        foreach ($entry in $savedServers) {
            Stop-ManagedServer $entry
        }
        Remove-Item -LiteralPath $stateFile
    }
    if ($Stop) { Write-Host 'Stopped managed FinWhiz servers.'; exit 0 }
    $portsToCheck = @($FrontendPort)
    if (-not $BackendUrl) { $portsToCheck += $BackendPort }
    foreach ($port in $portsToCheck) {
        $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
        if ($listener) {
            throw "Port $port is already in use by PID $($listener[0].OwningProcess). Stop that server in its terminal, then rerun restart.cmd. Untracked processes are not stopped automatically."
        }
    }
    if (-not $BackendUrl) {
        $BackendUrl = "http://127.0.0.1:$BackendPort"
        $backend = Start-Server $pythonPath @("`"$backendScript`"", 'serve', '--host', '127.0.0.1', '--port', $BackendPort) 'backend' $backendScript
        $started += $backend
        Save-Servers $started
        Wait-Healthy "$BackendUrl/api/health" $backend
    }
    $frontendArguments = @("`"$frontendScript`"", "--port=$FrontendPort", "--backend=$BackendUrl")
    if ($useSharedBackend) { $frontendArguments += '--remote-assets' }
    $frontend = Start-Server $nodePath $frontendArguments 'frontend' $frontendScript
    $started += $frontend
    Save-Servers $started
    Wait-Healthy "http://127.0.0.1:$FrontendPort/api/health" $frontend
    Write-Host "Frontend: http://127.0.0.1:$FrontendPort"
    Write-Host "Backend:  $BackendUrl"
    Write-Host "Logs:     $stateDirectory"
    Write-Host 'Servers run in the background. Stop with: .\restart.cmd -Stop'
} catch {
    foreach ($entry in $started) { try { Stop-ManagedServer $entry } catch { Write-Warning $_ } }
    if ($started.Count -gt 0) { Save-Servers @() }
    Write-Host "FinWhiz: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
