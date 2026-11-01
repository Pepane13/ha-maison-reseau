<#
.SYNOPSIS
    Weekly winget upgrade on this PC, with read-only reporting to Home Assistant through HASS.Agent.

.DESCRIPTION
    Run once, in an elevated PowerShell, from the session of the PC's usual user:
        powershell -ExecutionPolicy Bypass -File .\Setup-WingetWeekly.ps1                 # upgrade every Sunday 20:00
        powershell -ExecutionPolicy Bypass -File .\Setup-WingetWeekly.ps1 -ReportOnly     # never install, only report pending updates
        powershell -ExecutionPolicy Bypass -File .\Setup-WingetWeekly.ps1 -Exclude Some.App.Id,Other.App
    What it does:
      1. Copies the worker script to C:\ProgramData\HomeMonitor\winget-weekly.ps1.
      2. Pins the excluded packages (winget pin) so that "upgrade --all" leaves them alone.
      3. Creates two Windows scheduled tasks in the current user's context (winget is per user):
           - "Maison - winget hebdo"  : Sunday 20:00, upgrade --all --include-unknown (or check only with -ReportOnly),
                                        run as soon as possible if the PC was off;
           - "Maison - winget check"  : every day 12:00, list pending updates only.
      4. Adds three read-only sensors to the HASS.Agent Satellite service (pending count, pending list, last run report)
         and restarts it. Home Assistant can read the results but cannot trigger anything on the PC.
#>
[CmdletBinding()]
param(
    [switch]$ReportOnly,
    [string[]]$Exclude = @(),
    [string]$Day = 'Sunday',
    [string]$At = '20:00'
)
$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    !!  $m" -ForegroundColor Yellow }

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run from PowerShell "Run as administrator".' }
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw 'winget not found (install "App Installer" from the Microsoft Store).' }

$dir = Join-Path $env:ProgramData 'HomeMonitor'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$worker = Join-Path $dir 'winget-weekly.ps1'

Step 'Worker script'
@'
param([ValidateSet('Upgrade','Check')][string]$Mode = 'Check')
$ErrorActionPreference = 'Continue'
$dir = Join-Path $env:ProgramData 'HomeMonitor'
$out = Join-Path $dir 'winget-status.json'
$log = Join-Path $dir ("winget-{0:yyyyMMdd-HHmm}.log" -f (Get-Date))
function Get-Pending {
    # Parse "winget upgrade" table: Name  Id  Version  Available  Source
    $lines = winget upgrade --include-unknown --accept-source-agreements --disable-interactivity 2>$null | Out-String -Stream
    $hdr = $lines | Where-Object { $_ -match '^\s*(Name|Nom)\s+Id\s+' } | Select-Object -First 1
    if (-not $hdr) { return @() }
    $ix = { param($re) [regex]::Match($hdr, $re, 'IgnoreCase').Index }; $iId = & $ix '\bId\b'; $iVer = & $ix '\bVersion\b'; $iAv = & $ix '\b(Available|Disponible)\b'
    $start = [array]::IndexOf($lines, $hdr) + 2
    $res = @()
    foreach ($l in $lines[$start..($lines.Count - 1)]) {
        if ($l.Length -le $iAv -or $l -match '^\d+ (upgrades|mises)' -or $l -match '^-+$') { continue }
        $res += [pscustomobject]@{ Name = $l.Substring(0, $iId).Trim(); Id = $l.Substring($iId, $iVer - $iId).Trim(); Version = $l.Substring($iVer, $iAv - $iVer).Trim(); Available = ($l.Substring($iAv) -split '\s+')[0] }
    }
    $res | Where-Object { $_.Id -and $_.Id -notmatch '^-+$' }
}
$before = @(Get-Pending)
$status = [ordered]@{ date = (Get-Date).ToString('s'); mode = $Mode; pending_before = $before; upgraded = @(); failed = @(); pending = $before; exit_code = $null }
if ($Mode -eq 'Upgrade' -and $before.Count) {
    winget upgrade --all --include-unknown --silent --accept-package-agreements --accept-source-agreements --disable-interactivity *> $log
    $status.exit_code = $LASTEXITCODE
    $after = @(Get-Pending)
    $status.pending = $after
    $status.upgraded = @($before | Where-Object { $after.Id -notcontains $_.Id })
    $status.failed = @($before | Where-Object { $after.Id -contains $_.Id })
    Get-ChildItem $dir -Filter 'winget-*.log' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 8 | Remove-Item -Force
}
$prev = if (Test-Path $out) { Get-Content $out -Raw | ConvertFrom-Json } else { $null }
if ($Mode -eq 'Check' -and $prev -and $prev.last_upgrade) { $status.last_upgrade = $prev.last_upgrade }
if ($Mode -eq 'Upgrade') { $status.last_upgrade = [ordered]@{ date = $status.date; upgraded = $status.upgraded.Count; failed = @($status.failed | ForEach-Object Name) } }
# Plain ASCII texts for the HASS.Agent sensors (no quoting or encoding issues on their side)
$ascii = { param($t) (([string]$t).Normalize([Text.NormalizationForm]::FormD) -replace '\p{Mn}', '') -replace '[^\x20-\x7E]', '' }
$t = (@($status.pending) | ForEach-Object { '{0} {1}>{2}' -f $_.Name, $_.Version, $_.Available }) -join ' ; '
if (-not $t) { $t = 'aucune' } elseif ($t.Length -gt 250) { $t = $t.Substring(0, 247) + '...' }
$status.list_text = & $ascii $t
$u = $status.last_upgrade
$status.last_text = if (-not $u) { 'aucune execution' } else {
    $fl = @($u.failed); '{0:dd/MM HH:mm} - {1} mise(s) a jour - {2} echec(s){3}' -f ([datetime]$u.date), $u.upgraded, $fl.Count, $(if ($fl.Count) { ' : ' + ($fl -join ', ') } else { '' }) }
$status.last_text = & $ascii $status.last_text
$status | ConvertTo-Json -Depth 5 | Set-Content -Path $out -Encoding UTF8
'@ | Set-Content -Path $worker -Encoding UTF8
Ok $worker

if ($Exclude.Count) {
    Step 'Excluded packages (winget pin)'
    foreach ($id in $Exclude) { winget pin add --id $id --blocking --accept-source-agreements | Out-Null; Ok "pinned $id" }
}

Step 'Scheduled tasks'
$user = "$env:USERDOMAIN\$env:USERNAME"
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$prin = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$modeWeekly = if ($ReportOnly) { 'Check' } else { 'Upgrade' }
$aW = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$worker`" -Mode $modeWeekly"
$tW = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $At
Register-ScheduledTask -TaskName 'Maison - winget hebdo' -Action $aW -Trigger $tW -Principal $prin -Settings $settings -Force | Out-Null
Ok "Maison - winget hebdo : $Day $At ($modeWeekly)"
$aC = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$worker`" -Mode Check"
$tC = New-ScheduledTaskTrigger -Daily -At '12:00'
Register-ScheduledTask -TaskName 'Maison - winget check' -Action $aC -Trigger $tC -Principal $prin -Settings $settings -Force | Out-Null
Ok 'Maison - winget check : every day 12:00'

Step 'HASS.Agent sensors'
$svcName = 'hass.agent.svc'
$installPath = (Get-ItemProperty 'HKLM:\SOFTWARE\HASSAgent\SatelliteService' -ErrorAction SilentlyContinue).InstallPath
if (-not $installPath -or -not (Get-Service $svcName -ErrorAction SilentlyContinue)) {
    Warn 'HASS.Agent Satellite service not found: run Install-HassAgent.ps1 first. Scheduled tasks are in place anyway.'
} else {
    $sensorsFile = Join-Path $installPath 'config\sensors.json'
    $sensors = @(if (Test-Path $sensorsFile) { Get-Content $sensorsFile -Raw | ConvertFrom-Json })
    # Keep the Id of an existing winget sensor: a new Id would create a duplicate entity (_2) in Home Assistant.
    $keepId = @{}; foreach ($e in $sensors) { if ($e.EntityName -like 'winget_*' -and $e.Id) { $keepId[$e.EntityName] = $e.Id } }
    $sensors = @($sensors | Where-Object { $_.EntityName -notlike 'winget_*' })
    # Queries without double quotes: HASS.Agent passes them on the powershell command line, which strips double quotes.
    $read = '$f = Join-Path $env:ProgramData ''HomeMonitor\winget-status.json''; if (-not (Test-Path $f)) { ''jamais'' } else { $s = Get-Content $f -Raw | ConvertFrom-Json; '
    $qCount = $read + '@($s.pending).Count }'
    $qList = $read + '$s.list_text }'
    $qLast = $read + '$s.last_text }'
    function NewSensor($entity, $name, $query) {
        [pscustomobject][ordered]@{ Type = 'PowershellSensor'; Id = $(if ($keepId[$entity]) { $keepId[$entity] } else { [guid]::NewGuid().ToString() }); Name = $name; EntityName = $entity; UpdateInterval = 900; Query = $query; Scope = ''; WindowName = ''; Category = ''; Counter = ''; Instance = ''; IgnoreAvailability = $false; ApplyRounding = $false; Round = $null; AdvancedSettings = '' }
    }
    $sensors += NewSensor 'winget_en_attente' 'Mises à jour winget en attente' $qCount
    $sensors += NewSensor 'winget_liste' 'Liste des mises à jour winget' $qList
    $sensors += NewSensor 'winget_derniere_maj' 'Dernière mise à jour winget' $qLast
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    for ($i = 0; $i -lt 6 -and (Get-Service $svcName).Status -ne 'Stopped'; $i++) { try { Stop-Service $svcName -Force -ErrorAction Stop } catch { Start-Sleep 5 } }
    [IO.File]::WriteAllText($sensorsFile, (ConvertTo-Json @($sensors) -Depth 5), $utf8)
    Start-Service $svcName
    Ok '3 sensors added (winget_en_attente, winget_liste, winget_derniere_maj), service restarted'
}

Step 'First check (pending updates)'
& $ps -NoProfile -ExecutionPolicy Bypass -File $worker -Mode Check
$s = Get-Content (Join-Path $dir 'winget-status.json') -Raw | ConvertFrom-Json
Ok ("{0} update(s) pending" -f @($s.pending).Count)
@($s.pending) | Select-Object -First 15 | ForEach-Object { Write-Host ("    - {0} {1} -> {2}" -f $_.Name, $_.Version, $_.Available) }
Write-Host "`nDone." -ForegroundColor Green
