# Zaustavi i ukloni automatsko pokretanje radara.  powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1
$name = "Waiver Radar"
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Zadatak '$name' uklonjen."
} else {
    Write-Host "Zadatak '$name' ne postoji."
}
# proces koji je možda pokrenut ručno
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*-m radar serve*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Zaustavljen proces $($_.ProcessId)." }
