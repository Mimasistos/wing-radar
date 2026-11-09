@echo off
rem A2 Radar kurulumu: cift tikla. Python ve Npcap yoksa kurar, radari ayri bir sanal ortama yukler.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
