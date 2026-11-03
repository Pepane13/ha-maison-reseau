<#
.SYNOPSIS
    Installs HASS.Agent (Satellite service) and configures it for a Home Assistant security dashboard.

.DESCRIPTION
    Run in an elevated PowerShell (Run as administrator):
        powershell -ExecutionPolicy Bypass -File .\Install-HassAgent.ps1
    Optional parameters: -DeviceName PC-BUREAU -MqttHost homeassistant.local -MqttUser hass-agent

    What it does:
      1. Checks prerequisites (admin, MQTT broker reachable, .NET 8 Desktop runtime).
      2. Downloads the latest HASS.Agent installer from GitHub and verifies its Authenticode signature.
      3. Installs it silently (client + Satellite service).
      4. Writes the Satellite service configuration: device name, MQTT, read-only sensors.
         - A random AuthId is set: without it, any local process could reconfigure the service (which runs as SYSTEM).
         - No commands are configured: Home Assistant can read, never act on this PC.
         - The config folder is restricted to SYSTEM and Administrators (the MQTT password is stored there).
      5. Restarts the service and shows a summary.

    The MQTT password is asked interactively and is never written to this script or to the console.
#>
[CmdletBinding()]
param(
    [string]$DeviceName = $env:COMPUTERNAME,
    [string]$MqttHost   = 'homeassistant.local',
    [int]   $MqttPort   = 1883,
    [string]$MqttUser   = 'hass-agent',
    [int]   $Interval   = 300
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }

# --- 0. Prerequisites ---------------------------------------------------------------------------
Step 'Prerequisites'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from PowerShell opened with "Run as administrator".'
}
Ok 'Administrator rights'

$DeviceName = ($DeviceName -replace '[^A-Za-z0-9_-]', '-').Trim('-')
if (-not $DeviceName) { throw 'Invalid device name.' }
Ok "Device name: $DeviceName"

if ((Test-NetConnection -ComputerName $MqttHost -Port $MqttPort -WarningAction SilentlyContinue).TcpTestSucceeded) {
    Ok "MQTT broker reachable at ${MqttHost}:$MqttPort"
} else {
    Warn "MQTT broker not reachable at ${MqttHost}:$MqttPort (is the Mosquitto app installed and started in Home Assistant?). Installation continues; the agent will connect once the broker is up."
}

$secure = Read-Host "Password of the Home Assistant user '$MqttUser'" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $MqttPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if (-not $MqttPassword) { throw 'Empty password.' }

$hasDotNet8 = (& { try { dotnet --list-runtimes 2>$null } catch { @() } }) -match 'Microsoft\.WindowsDesktop\.App 8\.'
if (-not $hasDotNet8) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Step 'Installing .NET 8 Desktop Runtime (winget)'
        winget install --id Microsoft.DotNet.DesktopRuntime.8 -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
        Ok '.NET 8 Desktop Runtime installed'
    } else {
        Warn '.NET 8 Desktop Runtime missing and winget unavailable: the HASS.Agent installer will download it.'
    }
} else { Ok '.NET 8 Desktop Runtime present' }

# --- 1. Download --------------------------------------------------------------------------------
Step 'Downloading HASS.Agent (latest release)'
$work = Join-Path $env:TEMP 'hass-agent-setup'
New-Item -ItemType Directory -Force -Path $work | Out-Null
$installer = Join-Path $work 'HASS.Agent.Installer.exe'
Invoke-WebRequest -Uri 'https://github.com/hass-agent/HASS.Agent/releases/latest/download/HASS.Agent.Installer.exe' -OutFile $installer -UseBasicParsing
$sig = Get-AuthenticodeSignature $installer
# HASS.Agent signs with its own CA ("HASS.Agent Root CA"), not trusted by Windows: the status is then
# UnknownError (untrusted root) even though the file is intact. A modified file gives HashMismatch.
# We therefore accept it only if the signing certificate is exactly the known HASS.Agent one (pinned thumbprint, valid 2023-2033).
$pinnedThumbprint = 'F5E2C6F0BB7C78E82BFFE33ABC98A689BE690D10'
$signer = $sig.SignerCertificate
if (-not $signer) { throw "Installer is not signed ($($sig.Status)). Aborting." }
if ($sig.Status -eq 'HashMismatch') { throw 'Installer content does not match its signature (tampered or corrupted). Aborting.' }
if ($sig.Status -ne 'Valid' -and $signer.Thumbprint -ne $pinnedThumbprint) {
    throw "Unexpected signing certificate $($signer.Thumbprint) ($($signer.Subject), status $($sig.Status)). Aborting."
}
Ok ("Signed by: " + $signer.Subject + " [" + $signer.Thumbprint + "], status " + $sig.Status + ($(if ($sig.Status -ne 'Valid') { ' (self-signed HASS.Agent CA, certificate pinned)' } else { '' })))
Ok ("Version: " + (Get-Item $installer).VersionInfo.ProductVersion)

# --- 2. Silent install ----------------------------------------------------------------------------
Step 'Installing (client + Satellite service)'
$p = Start-Process -FilePath $installer -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-' -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Installer exit code $($p.ExitCode)." }
$svcName = 'hass.agent.svc'
$svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
if (-not $svc) { throw 'Satellite service not found after installation.' }
$installPath = (Get-ItemProperty 'HKLM:\SOFTWARE\HASSAgent\SatelliteService' -ErrorAction SilentlyContinue).InstallPath
if (-not $installPath) { $installPath = Join-Path $env:ProgramFiles 'HASS.Agent\Service' }
Ok "Service installed in $installPath"

# --- 3. Configuration -----------------------------------------------------------------------------
Step 'Configuring the Satellite service'
# The freshly installed service may still be starting and refuse a clean stop: retry, then kill its process.
for ($i = 0; $i -lt 6 -and (Get-Service $svcName).Status -ne 'Stopped'; $i++) {
    try { Stop-Service $svcName -Force -ErrorAction Stop } catch { Start-Sleep 5 }
}
if ((Get-Service $svcName).Status -ne 'Stopped') {
    $svcPid = (Get-CimInstance Win32_Service -Filter "Name='$svcName'").ProcessId
    if ($svcPid) { Stop-Process -Id $svcPid -Force -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 10 -and (Get-Service $svcName).Status -ne 'Stopped'; $i++) { Start-Sleep 2 }
}
if ((Get-Service $svcName).Status -ne 'Stopped') { throw 'The Satellite service could not be stopped. Reboot the PC and run the script again.' }
Ok 'Service stopped'
$cfg = Join-Path $installPath 'config'
New-Item -ItemType Directory -Force -Path $cfg | Out-Null

# Lock the folder before writing secrets: SYSTEM + Administrators only.
icacls $cfg /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null

$utf8 = New-Object System.Text.UTF8Encoding($false)
function Write-Json($name, $obj) { [IO.File]::WriteAllText((Join-Path $cfg $name), ($obj | ConvertTo-Json -Depth 5), $utf8) }

Write-Json 'servicesettings.json' ([ordered]@{
    AuthId                         = [guid]::NewGuid().ToString()
    DeviceName                     = $DeviceName
    CustomExecutorName             = ''
    CustomExecutorBinary           = ''
    DisconnectedGracePeriodSeconds = 60
})

Write-Json 'servicemqttsettings.json' ([ordered]@{
    MqttAddress                    = $MqttHost
    MqttPort                       = $MqttPort
    MqttUseWebSocket               = $false
    MqttUseTls                     = $false
    MqttAllowUntrustedCertificates = $false
    MqttUsername                   = $MqttUser
    MqttPassword                   = $MqttPassword
    MqttDiscoveryPrefix            = 'homeassistant'
    MqttClientId                   = "hass-agent-$DeviceName".ToLower()
    MqttUseRetainFlag              = $true
    MqttRootCertificate            = ''
    MqttClientCertificate          = ''
})
Remove-Variable MqttPassword

# Security Center productState: 2nd byte 0x10/0x11 = enabled, 3rd byte 0x00 = definitions up to date.
$psAntivirus = @'
$p = Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Sort-Object { $_.displayName -notlike '*Bitdefender*' } | Select-Object -First 1
if (-not $p) { 'absent' } else { $s = '{0:X6}' -f [int]$p.productState
  if ($s.Substring(2,2) -notin '10','11') { 'inactif' } elseif ($s.Substring(4,2) -ne '00') { 'obsolete' } else { 'actif' } }
'@
$psFirewall = @'
$p = Get-CimInstance -Namespace root/SecurityCenter2 -ClassName FirewallProduct | Select-Object -First 1
if ($p) { $s = '{0:X6}' -f [int]$p.productState; if ($s.Substring(2,2) -in '10','11') { 'actif' } else { 'inactif' } }
elseif (@(Get-NetFirewallProfile | Where-Object { -not $_.Enabled }).Count) { 'inactif' } else { 'actif' }
'@
$psAvName = @'
(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Select-Object -ExpandProperty displayName) -join ', '
'@
$psBitlocker = @'
$v = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction SilentlyContinue
if (-not $v) { 'inconnu' }
elseif ($v.ProtectionStatus -eq 'On') { 'actif' }
elseif ($v.VolumeStatus -in 'FullyEncrypted','EncryptionInProgress') { 'suspendu' }
else { 'inactif' }
'@

# Re-running the script must keep each sensor's Id: a new Id is a new MQTT unique_id, which leaves
# a stale duplicate (entity_id suffixed _2) in Home Assistant. Sensors added by other scripts are kept.
$sensorsPath = Join-Path $cfg 'sensors.json'
function Read-SensorList($path) {
    # Returns the HASS.Agent sensor list as a flat sequence of objects.
    # - Windows PowerShell 5.1 emits a JSON array from ConvertFrom-Json as ONE object: wrapping it in @()
    #   and appending to it nested the list, which ConvertTo-Json then wrote as {"value":[...],"Count":n}
    #   (HASS.Agent could no longer read its sensors). Both nested forms are unwrapped here.
    # - The file is read as UTF-8 explicitly, and names already double-encoded (UTF-8 read as ANSI) are repaired.
    if (-not (Test-Path $path)) { return }
    $raw = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $latin1 = [Text.Encoding]::GetEncoding(28591)
    $queue = New-Object System.Collections.Queue
    foreach ($item in @($raw)) { $queue.Enqueue(@($item, 0)) }
    while ($queue.Count -gt 0) {
        $pair = $queue.Dequeue(); $it = $pair[0]; $lvl = $pair[1]
        if ($lvl -gt 3 -or -not $it) { continue }
        if ($it -is [array]) { foreach ($sub in $it) { $queue.Enqueue(@($sub, ($lvl + 1))) } }
        elseif (($it.PSObject.Properties.Name -contains 'value') -and -not ($it.PSObject.Properties.Name -contains 'Type')) {
            foreach ($sub in @($it.value)) { $queue.Enqueue(@($sub, ($lvl + 1))) }
        }
        elseif ($it.Type) {
            if ("$($it.Name)".Contains([string][char]0xC3)) { $it.Name = [Text.Encoding]::UTF8.GetString($latin1.GetBytes($it.Name)) }
            $it
        }
    }
}
$existing = @(try { Read-SensorList $sensorsPath } catch { Warn "sensors.json unreadable, rebuilt: $($_.Exception.Message)" })
$keepId = @{}; foreach ($e in $existing) { if ($e.EntityName -and $e.Id) { $keepId[$e.EntityName] = $e.Id } }
function Sensor($type, $entity, $name, $query = '') {
    [ordered]@{
        Type = $type; Id = $(if ($keepId[$entity]) { $keepId[$entity] } else { [guid]::NewGuid().ToString() }); Name = $name; EntityName = $entity
        UpdateInterval = $Interval; Query = $query; Scope = ''; WindowName = ''; Category = ''; Counter = ''; Instance = ''
        IgnoreAvailability = $false; ApplyRounding = $false; Round = $null; AdvancedSettings = ''
    }
}
$sensors = @(
    (Sensor 'PowershellSensor'     'antivirus'      'Antivirus'        $psAntivirus),
    (Sensor 'PowershellSensor'     'antivirus_nom'  'Antivirus (nom)'  $psAvName),
    (Sensor 'PowershellSensor'     'pare_feu'       'Pare-feu'         $psFirewall),
    (Sensor 'PowershellSensor'     'bitlocker'      'Chiffrement disque' $psBitlocker),
    (Sensor 'WindowsUpdatesSensors' 'windows_updates' 'Windows Update'),
    (Sensor 'LastBootSensor'       'dernier_demarrage' "Dernier d$([char]0xE9)marrage"),
    (Sensor 'LoggedUsersSensor'    'sessions'       'Sessions ouvertes')
)
$mine = @($sensors | ForEach-Object { $_.EntityName })
$sensors = @($sensors) + @($existing | Where-Object { $_.EntityName -and $mine -notcontains $_.EntityName })
[IO.File]::WriteAllText($sensorsPath, (ConvertTo-Json @($sensors) -Depth 5), $utf8)
[IO.File]::WriteAllText((Join-Path $cfg 'commands.json'), '[]', $utf8)
Ok "$($sensors.Count) read-only sensors configured, no commands"

# --- 4. Start -------------------------------------------------------------------------------------
Step 'Starting the service'
Set-Service $svcName -StartupType Automatic
Start-Service $svcName
Start-Sleep 10
$state = (Get-Service $svcName).Status
if ($state -eq 'Running') { Ok 'Service running' } else { Warn "Service state: $state" }

# Local self-test of the security sensors (same scripts, current user context)
Step 'Local check of the security sensors'
foreach ($t in @(@('Antivirus', $psAntivirus), @('Antivirus (nom)', $psAvName), @('Pare-feu', $psFirewall), @('Chiffrement disque', $psBitlocker))) {
    $v = try { (& ([scriptblock]::Create($t[1]))) } catch { "erreur: $($_.Exception.Message)" }
    Write-Host ("    {0,-20} {1}" -f $t[0], $v)
}

$log = Get-ChildItem (Join-Path $installPath 'logs') -Filter *.log -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
if ($log) {
    $mqtt = Select-String -Path $log.FullName -Pattern 'MQTT' | Select-Object -Last 3
    if ($mqtt) { Write-Host "`n    Last MQTT log lines:"; $mqtt | ForEach-Object { Write-Host "    $($_.Line)" } }
}

Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "`nDone. In Home Assistant, the device '$DeviceName' should appear under Settings > Devices & services > MQTT within a minute." -ForegroundColor Green
Write-Host "The HASS.Agent desktop client was installed too but is not needed: do not configure commands in it." -ForegroundColor Green
