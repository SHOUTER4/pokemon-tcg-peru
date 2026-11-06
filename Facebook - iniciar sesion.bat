@echo off
rem Abre un Chrome aparte para iniciar sesion en Facebook (solo la primera vez o si vence).
rem Ese Chrome lo usa la tarea "TCG Subastas Facebook" para LEER las pujas de tus subastas.
chcp 65001 >nul
cd /d "%~dp0"
title Facebook - iniciar sesion
echo Se abrira una ventana de Chrome en Facebook.
echo Inicia sesion ahi (con tu usuario y clave) y luego CIERRA esa ventana.
echo.
python fb_subastas.py --login
echo.
echo Listo. Creando la tarea que lee las subastas cada 5 minutos...
python fb_subastas.py --instalar
echo Probando una lectura ahora...
python fb_subastas.py --todas
pause
