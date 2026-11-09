@echo off
chcp 65001 >nul
cd /d "%~dp0"
net session >nul 2>&1 || (powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs" & exit /b)

where py >nul 2>&1 || (
  echo [!] Python bulunamadi. Once install.bat dosyasini calistir ^(Python ve Npcap'i kurar^).
  pause
  exit /b 1
)

py -c "import scapy, lz4, psutil, PIL" >nul 2>&1 || (
  echo [+] Eksik bagimliliklar kuruluyor ^(bir kez^)...
  py -m pip install --disable-pip-version-check -r requirements.txt || (
    echo [!] Bagimliliklar kurulamadi. install.bat dosyasini dene.
    pause
    exit /b 1
  )
)

py -m a2radar || pause
