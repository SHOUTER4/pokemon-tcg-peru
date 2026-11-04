@echo off
rem Ejecuta el escaneo de tiendas y abre el reporte al terminar.
chcp 65001 >nul
cd /d "%~dp0"
title Escaneo Pokemon TCG Peru
echo Escaneando tiendas de Pokemon TCG... (puede tardar unos minutos)
echo.
python scanner.py %*
if errorlevel 1 (
    echo.
    echo El escaneo termino con error. Revisa data\escaneo.log
) else (
    start "" "%~dp0reportes\ultimo.html"
)
echo.
pause
