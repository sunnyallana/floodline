<#
Keeps a resumable floodline stage running on Windows until it has finished, whatever interrupts it (RDP
disconnects, console signals, crashes, reboots). Run it every few minutes from the Task Scheduler:

  schtasks /Create /TN FloodlineVLM /SC MINUTE /MO 10 /F /TR `
    "powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\floodline\scripts\watchdog.ps1 `
     -Name vlm -Args 'assess' -DoneFile results\vlm_assessments.jsonl -DoneLines 538 -KillFirst llama-server"

The stage must be resumable (floodline's long stages append to JSONL/CSV and skip finished items), because a
restart simply runs the same command again. When -DoneFile has -DoneLines lines, the task deletes itself.
#>
param(
    [Parameter(Mandatory)] [string] $Name,
    [Parameter(Mandatory)] [string] $Args,
    [Parameter(Mandatory)] [string] $DoneFile,
    [Parameter(Mandatory)] [int] $DoneLines,
    [string] $KillFirst = "",
    [string] $TaskName = ""
)
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$log = Join-Path $logs "$Name-watchdog.log"
$pidFile = Join-Path $logs "$Name.pid"
function Log($m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) }

$done = Join-Path $root $DoneFile
if ((Test-Path $done) -and ((Get-Content $done | Measure-Object -Line).Lines -ge $DoneLines)) {
    Log "complete ($DoneLines lines in $DoneFile)"
    if ($TaskName) { schtasks /Delete /TN $TaskName /F | Out-Null; Log "task $TaskName removed" }
    exit 0
}
if (Test-Path $pidFile) {
    $runner = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($runner -and (Get-Process -Id $runner -ErrorAction SilentlyContinue)) { exit 0 }
    Log "pid $runner not running; restarting"
}
if ($KillFirst) {
    # A child server left behind by a killed runner still holds its port and would block the restart.
    Get-Process -Name $KillFirst -ErrorAction SilentlyContinue | ForEach-Object { Log "stopping stray $KillFirst $($_.Id)"; Stop-Process -Id $_.Id -Force }
}
$env:HF_TOKEN = [Environment]::GetEnvironmentVariable("HF_TOKEN", "User")
$exe = Join-Path $root ".venv\Scripts\floodline.exe"
$p = Start-Process -FilePath $exe -ArgumentList $Args -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logs "$Name.out.log") -RedirectStandardError (Join-Path $logs "$Name.err.log")
Start-Sleep -Seconds 5
$child = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($p.Id)" | Where-Object { $_.Name -eq "python.exe" } | Select-Object -First 1
$target = if ($child) { $child.ProcessId } else { $p.Id }
Set-Content -Path $pidFile -Value $target
Log "started $Name ($Args) pid $target"
