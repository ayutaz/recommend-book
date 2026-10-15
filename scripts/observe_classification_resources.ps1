param(
    [Parameter(Mandatory=$true)][string]$Output,
    [string]$CommandMatch = 'scripts/benchmark_classification.py',
    [int]$IntervalSeconds = 10,
    [int]$MaximumSeconds = 7200
)

if ($IntervalSeconds -lt 1 -or $MaximumSeconds -lt 1) { throw 'Positive observation bounds required' }
$taskOutputPath = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $taskOutputPath) { throw 'Observation output must be new' }
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($taskOutputPath)) | Out-Null
$taskStartedAt = Get-Date
$taskSeenTarget = $false
$taskTargetPids = @()
while (((Get-Date) - $taskStartedAt).TotalSeconds -lt $MaximumSeconds) {
    $taskInventory = @(Get-CimInstance Win32_Process)
    if (-not $taskSeenTarget) {
        $taskTargetPids = @($taskInventory | Where-Object {
            $_.Name -eq 'python.exe' -and $_.CommandLine -like "*$CommandMatch*"
        } | ForEach-Object { [int]$_.ProcessId })
        if ($taskTargetPids.Count -gt 0) { $taskSeenTarget = $true }
    }
    $taskLiveRoots = @($taskInventory | Where-Object { [int]$_.ProcessId -in $taskTargetPids })
    if ($taskSeenTarget -and $taskLiveRoots.Count -eq 0) { break }
    $taskDescendants = @($taskLiveRoots | ForEach-Object { [int]$_.ProcessId })
    do {
        $taskNewPids = @($taskInventory | Where-Object {
            [int]$_.ParentProcessId -in $taskDescendants -and [int]$_.ProcessId -notin $taskDescendants
        } | ForEach-Object { [int]$_.ProcessId })
        $taskDescendants += $taskNewPids
    } while ($taskNewPids.Count -gt 0)
    $taskSamples = @()
    foreach ($taskObservedPid in $taskDescendants) {
        $taskProcess = Get-Process -Id $taskObservedPid -ErrorAction SilentlyContinue
        if ($null -ne $taskProcess) {
            $taskSamples += @{
                pid = $taskProcess.Id
                name = $taskProcess.ProcessName
                cpu_seconds = $taskProcess.CPU
                working_set_bytes = $taskProcess.WorkingSet64
                private_bytes = $taskProcess.PrivateMemorySize64
                threads = $taskProcess.Threads.Count
            }
        }
    }
    $taskRow = @{
        observed_at = (Get-Date).ToUniversalTime().ToString('o')
        elapsed_seconds = ((Get-Date) - $taskStartedAt).TotalSeconds
        target_seen = $taskSeenTarget
        samples = $taskSamples
        measurement_scope = 'Windows scheduler process and currently live descendants; sampled values, not peak or final CPU totals; exited children may be missed'
    }
    [IO.File]::AppendAllText($taskOutputPath, (($taskRow | ConvertTo-Json -Depth 5 -Compress) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    Start-Sleep -Seconds $IntervalSeconds
}
@{ output = $taskOutputPath; target_seen = $taskSeenTarget; elapsed_seconds = ((Get-Date) - $taskStartedAt).TotalSeconds } | ConvertTo-Json -Compress
