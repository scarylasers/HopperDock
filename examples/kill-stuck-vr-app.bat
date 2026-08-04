@echo off
REM ---------------------------------------------------------------------
REM  Force-close a VR game that didn't actually quit.
REM
REM  Some Meta/Oculus store titles leave their window (and process) running
REM  after you quit inside the headset. The game is gone from your view but
REM  still holding the headset, your mic, and a chunk of GPU — which is
REM  especially annoying mid-stream when you want to switch titles.
REM
REM  This closes it properly. Population One is the example because it does
REM  this constantly; change the name below for any other game.
REM
REM  To find a game's process name: open Task Manager -> Details tab, and
REM  look at the Name column while the game is running.
REM ---------------------------------------------------------------------

set "GAME=PopulationOne.exe"

tasklist /fi "imagename eq %GAME%" 2>nul | find /i "%GAME%" >nul
if errorlevel 1 (
    echo %GAME% is not running - nothing to close.
) else (
    taskkill /f /im "%GAME%"
    echo Closed %GAME%.
)

REM Remove the line below if you'd rather the window stayed open.
exit
