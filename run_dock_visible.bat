@echo off
REM Debug launcher — same script as run_dock.bat, but with a console attached
REM so tracebacks and stdout are visible.
python "%~dp0window_dock.pyw"
pause
