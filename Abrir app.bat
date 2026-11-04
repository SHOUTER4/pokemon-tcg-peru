@echo off
rem Abre la app interactiva en el navegador. Mientras esta ventana siga abierta, la app funciona.
chcp 65001 >nul
cd /d "%~dp0"
title Pokemon TCG Peru (app)
echo Abriendo la app en tu navegador...
echo Deja esta ventana abierta mientras la usas. Cierrala para apagar la app.
echo.
python app.py
pause
