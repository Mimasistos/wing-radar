@echo off
chcp 65001 >nul
cd /d "%~dp0"
net session >nul 2>&1 || (powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs" & exit /b)
py -m a2radar || pause
