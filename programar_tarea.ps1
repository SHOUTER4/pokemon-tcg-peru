# Crea (o reemplaza) una tarea del Programador de tareas de Windows que ejecuta
# scanner.py todos los días a la hora indicada.
#   powershell -ExecutionPolicy Bypass -File programar_tarea.ps1            # 09:00
#   powershell -ExecutionPolicy Bypass -File programar_tarea.ps1 -Hora 20:30
#   powershell -ExecutionPolicy Bypass -File programar_tarea.ps1 -Quitar   # borra la tarea
param(
    [string]$Hora = "09:00",
    [switch]$Quitar
)

$Nombre = "EscaneoPokemonTCGPeru"

if ($Quitar) {
    Unregister-ScheduledTask -TaskName $Nombre -Confirm:$false
    Write-Host "Tarea '$Nombre' eliminada."
    return
}

$Carpeta = $PSScriptRoot
$Python = (Get-Command python).Source
$Accion = New-ScheduledTaskAction -Execute $Python -Argument "`"$Carpeta\scanner.py`"" -WorkingDirectory $Carpeta
$Disparador = New-ScheduledTaskTrigger -Daily -At $Hora
# StartWhenAvailable: si la PC estaba apagada a esa hora, corre al encenderla.
$Ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $Nombre -Action $Accion -Trigger $Disparador -Settings $Ajustes `
    -Description "Escanea tiendas peruanas de Pokemon TCG (scanner.py)" -Force | Out-Null

Write-Host "Tarea '$Nombre' programada todos los días a las $Hora."
Write-Host "Probarla ahora: Start-ScheduledTask -TaskName $Nombre"
