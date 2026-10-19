$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $Project "start_market_radar.cmd"
$Startup = [Environment]::GetFolderPath("Startup")
$Autostart = Join-Path $Startup "MarketRadar.cmd"
$Content = "@echo off`r`nstart `"`" `"$Launcher`"`r`n"
Set-Content -Path $Autostart -Value $Content -Encoding Ascii
Write-Host "MarketRadar autostart installed: $Autostart"
