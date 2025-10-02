# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

# This PowerShell script enables the use of an Ethernet and WI-FI interface at the same time.

# Run in PowerShell **as Administrator**
$ssid = "TvLand"
$if   = "Wi-Fi"

# 1) Make sure the Wi-Fi service is enabled
Set-Service WlanSvc -StartupType Automatic
Start-Service WlanSvc

# 2) Auto-connect to TvLand and make it top priority on Wi-Fi
netsh wlan set profileparameter name="$ssid" connectionmode=auto | Out-Null
netsh wlan set profileorder     name="$ssid" interface="$if" priority=1 | Out-Null

# 3) Allow Ethernet + Wi-Fi at the same time (disable "minimize connections")
New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WcmSvc\GroupPolicy" -Force | Out-Null
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WcmSvc\GroupPolicy" `
  -Name "fMinimizeConnections" -Type DWord -Value 0

# 4) Reconnect to TvLand automatically at user logon (in case Windows dropped Wi-Fi)
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -WindowStyle Hidden -Command `"Start-Sleep 5; netsh wlan connect name='$ssid' interface='$if'`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName 'ConnectTvLandOnLogon' -Action $action -Trigger $trigger -RunLevel Highest -Force
