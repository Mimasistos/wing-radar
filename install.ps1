<#
.SYNOPSIS
  A2 Radar kurulumu (Windows): Python 3.10+ ve Npcap'i kontrol eder/kurar, radari ayri bir sanal ortama kurar,
  masaustune "yonetici olarak calisan" kisayol koyar.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Mimasistos/wing-radar/main/install.ps1 | iex"

.PARAMETER Check      Hicbir sey kurmaz, sadece durumu raporlar.
.PARAMETER DataFrom   Harita verisi klasoru (altgard_cubes.json, altgard_map.png, landmarks.json); %APPDATA%\A2Radar'a kopyalanir.
.PARAMETER Uninstall  Sanal ortami ve kisayolu siler (Python/Npcap/ayarlar kalir).
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'A2Radar'),
    [string]$Source = 'https://github.com/Mimasistos/wing-radar/archive/refs/heads/main.zip',
    [string]$DataFrom = '',
    [switch]$Check,
    [switch]$SkipPrereqs,
    [switch]$NoShortcut,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$RawUrl = 'https://raw.githubusercontent.com/Mimasistos/wing-radar/main/install.ps1'
$WantedPy = @('3.12', '3.13', '3.11', '3.10')     # 3.12 once: paketlerin hazir tekerlekleri var

function Info($m) { Write-Host "[+] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Done($code) {
    if (-not $Check -and [Environment]::UserInteractive) { try { [void](Read-Host 'Kapatmak icin Enter') } catch {} }
    exit $code
}
function Fail($m) { Write-Host "[HATA] $m" -ForegroundColor Red; Done 1 }

function Test-Admin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Gerekirse (Python/Npcap kurulacaksa) yonetici olarak yeniden baslatir. Kurulacak bir sey yoksa UAC sorulmaz.
function Ensure-Admin {
    if (Test-Admin) { return }
    Warn 'Bu adim yonetici yetkisi ister; yonetici olarak yeniden baslatiliyor (UAC penceresini onayla)...'
    if ($PSCommandPath) {
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
        foreach ($k in $PSBoundParameters.Keys) {
            $v = $PSBoundParameters[$k]
            if ($v -is [switch]) { if ($v.IsPresent) { $a += "-$k" } } else { $a += "-$k"; $a += "`"$v`"" }
        }
        Start-Process powershell -Verb RunAs -ArgumentList $a
    } else {
        Start-Process powershell -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "irm $RawUrl | iex")
    }
    exit 0
}

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Find-Python {
    foreach ($v in $WantedPy) {
        try {
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path ($exe | Select-Object -First 1))) { return ($exe | Select-Object -First 1) }
        } catch {}
    }
    return $null
}

function Test-Npcap {
    if (Get-Service npcap -ErrorAction SilentlyContinue) { return $true }
    return (Test-Path 'HKLM:\SOFTWARE\Npcap')
}

function Install-Python {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Start-Process 'https://www.python.org/downloads/windows/'
        Fail "winget bulunamadi. Python 3.12'yi elle kur (https://www.python.org/downloads/windows/, 'Add python.exe to PATH' ve 'py launcher' isaretli), sonra bu betigi tekrar calistir."
    }
    Ensure-Admin
    Info 'Python 3.12 kuruluyor (winget)...'
    & winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

function Install-Npcap {
    Ensure-Admin
    Info 'Npcap resmi siteden (npcap.com) indiriliyor...'
    $url = $null
    try {
        $html = (Invoke-WebRequest -Uri 'https://npcap.com/' -UseBasicParsing -TimeoutSec 30).Content
        $m = [regex]::Match($html, '(?i)href="(?:https://npcap\.com/)?(dist/npcap-(\d+(?:\.\d+)+)\.exe)"')
        if ($m.Success) { $url = 'https://npcap.com/' + $m.Groups[1].Value }
    } catch {}
    if (-not $url) {
        Start-Process 'https://npcap.com/#download'
        Fail "Npcap indirme baglantisi bulunamadi. Acilan sayfadan Npcap'i elle kur, sonra bu betigi tekrar calistir."
    }
    $file = Join-Path $env:TEMP ([IO.Path]::GetFileName($url))
    Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing
    $sig = Get-AuthenticodeSignature $file
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Insecure\.Com') {
        Remove-Item $file -Force -ErrorAction SilentlyContinue
        Fail "Indirilen dosyanin dijital imzasi dogrulanamadi ($($sig.Status)); calistirilmadi. https://npcap.com/#download adresinden elle kur."
    }
    Ok "Imza dogrulandi ($($sig.SignerCertificate.Subject.Split(',')[0]))."
    Warn "Npcap kurulum penceresi aciliyor. Lisansi kendin onayla; 'Install Npcap in WinPcap API-compatible Mode' kutusu isaretli kalsin."
    Start-Process -FilePath $file -Wait
    Remove-Item $file -Force -ErrorAction SilentlyContinue
}

function New-AdminShortcut($path, $target, $arguments, $workDir) {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($path)
    $lnk.TargetPath = $target
    $lnk.Arguments = $arguments
    $lnk.WorkingDirectory = $workDir
    $lnk.Description = 'A2 Radar'
    $lnk.Save()
    $bytes = [IO.File]::ReadAllBytes($path)
    $bytes[0x15] = $bytes[0x15] -bor 0x20          # "Yonetici olarak calistir" bayragi (paket yakalama icin gerekir)
    [IO.File]::WriteAllBytes($path, $bytes)
}

# ---------------------------------------------------------------- Kaldirma
$venv = Join-Path $InstallDir 'venv'
$shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'A2 Radar.lnk'

if ($Uninstall) {
    if (Test-Path $venv) { Remove-Item $venv -Recurse -Force; Ok "Sanal ortam silindi: $venv" } else { Info 'Sanal ortam yok.' }
    if (Test-Path $shortcut) { Remove-Item $shortcut -Force; Ok 'Masaustu kisayolu silindi.' }
    Info "Python, Npcap ve ayarlar (%APPDATA%\A2Radar) yerinde birakildi."
    Done 0
}

# ---------------------------------------------------------------- Durum
Write-Host ''
Write-Host '=== A2 Radar kurulumu ===' -ForegroundColor White
$py = Find-Python
if ($py) { Ok "Python bulundu: $py" } else { Warn 'Python 3.10+ bulunamadi.' }
$npcap = Test-Npcap
if ($npcap) { Ok 'Npcap kurulu.' } else { Warn 'Npcap kurulu degil.' }
$installed = Test-Path (Join-Path $venv 'Scripts\python.exe')
if ($installed) { Ok "Onceki kurulum var: $venv (guncellenecek)" }
if ($Check) {
    Write-Host ''
    Info 'Kontrol modu: hicbir sey kurulmadi.'
    Done 0
}

# ---------------------------------------------------------------- Onkosullar
if (-not $SkipPrereqs) {
    if (-not $py) {
        Install-Python
        $py = Find-Python
        if (-not $py) { Fail 'Python kuruldu ama bulunamadi. Yeni bir PowerShell penceresi acip betigi tekrar calistir.' }
        Ok "Python hazir: $py"
    }
    if (-not $npcap) {
        Install-Npcap
        if (Test-Npcap) { Ok 'Npcap kuruldu.' } else { Warn 'Npcap kurulumu tamamlanmadi; radar paket yakalayamaz. Betigi tekrar calistirabilirsin.' }
    }
} elseif (-not $py) {
    Fail 'Python bulunamadi (-SkipPrereqs verildi).'
}

# ---------------------------------------------------------------- Sanal ortam + paket
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    Info "Sanal ortam olusturuluyor: $venv"
    & $py -m venv $venv
    if ($LASTEXITCODE -ne 0) { Fail 'Sanal ortam olusturulamadi.' }
}
$vpy = Join-Path $venv 'Scripts\python.exe'
Info 'Bagimliliklar ve radar kuruluyor (pip)...'
& $vpy -m pip install --quiet --disable-pip-version-check --upgrade pip
& $vpy -m pip install --quiet --disable-pip-version-check --upgrade $Source
if ($LASTEXITCODE -ne 0) { Fail 'pip kurulumu basarisiz oldu (internet baglantisini kontrol et).' }
$ver = & $vpy -c "import a2radar; print(a2radar.__version__)"
if ($LASTEXITCODE -ne 0) { Fail 'Kurulum dogrulanamadi (a2radar iceri aktarilamadi).' }
Ok "A2 Radar $ver kuruldu."

# ---------------------------------------------------------------- Veri (istege bagli)
if ($DataFrom) {
    if (-not (Test-Path $DataFrom)) { Fail "Veri klasoru bulunamadi: $DataFrom" }
    $dst = Join-Path $env:APPDATA 'A2Radar'
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    foreach ($f in 'altgard_cubes.json', 'altgard_map.png', 'landmarks.json') {
        $p = Join-Path $DataFrom $f
        if (Test-Path $p) { Copy-Item $p $dst -Force; Ok "Veri kopyalandi: $f" }
    }
}

# ---------------------------------------------------------------- Kisayol
if (-not $NoShortcut) {
    $pyw = Join-Path $venv 'Scripts\pythonw.exe'
    New-AdminShortcut $shortcut $pyw '-m a2radar' $InstallDir
    Ok "Masaustu kisayolu olusturuldu (yonetici olarak calisir): $shortcut"
}

Write-Host ''
Ok 'Kurulum tamam. Masaustundeki "A2 Radar" kisayolunu ac (UAC onayi isteyecek).'
if (-not (Test-Path (Join-Path $env:APPDATA 'A2Radar\altgard_cubes.json'))) {
    Info "Rota/yol haritasi icin veri dosyalari gerekir (depoda yok). Dosyalari %APPDATA%\A2Radar klasorune koy veya -DataFrom kullan."
}
Done 0
