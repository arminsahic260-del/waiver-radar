# Waiver Radar 2.1: pokreni `python -m radar serve` automatski pri prijavi u Windows (bez prozora), s ponovnim
# pokretanjem ako se proces sruši. Pokreni jednom:  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { throw "Nema $pyw. Prvo: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt" }

$name = "Waiver Radar"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "-m radar serve" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Waiver Radar 2.1: alarmi, provjere prije utakmica, Telegram bot, dashboard (python -m radar serve)" -Force | Out-Null
Start-ScheduledTask -TaskName $name
Start-Sleep -Seconds 3
$info = Get-ScheduledTaskInfo -TaskName $name
Write-Host "Zadatak '$name' registriran i pokrenut (stanje: $((Get-ScheduledTask -TaskName $name).State), zadnji rezultat: $($info.LastTaskResult))."
Write-Host "Log: $root\data\local\serve.log"
