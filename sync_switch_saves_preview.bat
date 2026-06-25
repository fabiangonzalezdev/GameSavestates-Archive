@echo off
setlocal
cd /d "%~dp0"
python "SAVES\Tools\sync_switch_saves.py" %*
pause
