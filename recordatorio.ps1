# Aviso emergente con sonido (lo usan las tareas programadas de subastas).
# "Si" abre el link de la subasta en el navegador.
param([string]$Titulo = "Recordatorio", [string]$Mensaje = "", [string]$Url = "")

Add-Type -AssemblyName System.Windows.Forms
[System.Media.SystemSounds]::Exclamation.Play()
$dueno = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true }
$texto = $Mensaje
if ($Url) { $texto += "`n`nAbrir la subasta ahora?" }
$botones = if ($Url) { "YesNo" } else { "OK" }
$r = [System.Windows.Forms.MessageBox]::Show($dueno, $texto, $Titulo, $botones, "Warning")
if ($r -eq "Yes") { Start-Process $Url }
