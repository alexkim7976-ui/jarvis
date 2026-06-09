# J.A.R.V.I.S — Windows 자동 설치
# 사용: .\install.ps1  또는  Install JARVIS.bat 더블클릭
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($msg) { Write-Host "▶ $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "✓ $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "⚠ $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "✗ $msg" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════╗"
Write-Host "║   J.A.R.V.I.S  ·  Windows 자동 설치              ║"
Write-Host "╚══════════════════════════════════════════════════╝"
Write-Host ""

# ── 1. Python ──
Step "환경 확인"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    $py = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Step "Python 설치 (winget)"
        winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path", "User")
        $py = Get-Command python -ErrorAction SilentlyContinue
    }
}
if (-not $py) {
    Fail "Python 3.9+ 없음. https://www.python.org/downloads/ 에서 설치 후 'Add to PATH' 체크"
}

$pyVer = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$pyVer -lt [version]"3.9") {
    Fail "Python 3.9+ 필요 (현재: $pyVer)"
}
Ok "Python $pyVer"

# ── 2. 시스템 도구 (winget, 선택) ──
function Install-Winget {
    param([string]$Id, [string]$Name)
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
    $installed = winget list --id $Id -e 2>$null | Select-String $Id
    if ($installed) { Ok "winget: $Name (설치됨)"; return $true }
    Step "winget install $Name"
    try {
        winget install --id $Id -e --accept-source-agreements --accept-package-agreements | Out-Null
        Ok "winget: $Name"
        return $true
    } catch {
        Warn "winget: $Name 설치 실패 — 수동 설치 필요"
        return $false
    }
}

Step "Windows 시스템 의존성 (winget)"
Install-Winget "Gyan.FFmpeg" "FFmpeg (TTS 재생)"
Install-Winget "Microsoft.OpenJDK.21" "OpenJDK 21"
Install-Winget "Apache.Maven" "Maven"
Install-Winget "Streamlink.Streamlink" "Streamlink (IR 통역, 선택)" | Out-Null

# PATH 갱신
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

# ── 3. 가상환경 ──
Step "Python 가상환경 (.venv)"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Ok "가상환경 생성"
} else {
    Ok "가상환경 존재"
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { Fail ".venv 생성 실패" }

& $venvPython -m pip install -U pip wheel setuptools -q
Ok "pip 업그레이드"

Step "Python 패키지 설치 (수 분 소요)"
$reqFile = if (Test-Path "requirements-windows.txt") { "requirements-windows.txt" } else { "requirements.txt" }
& $venvPython -m pip install -r $reqFile -q
Ok "$reqFile 설치 완료"

# ── 4. 디렉터리 / .env ──
Step "데이터 디렉터리"
@("audio_cache", "faces", "voice_profile", ".logs", ".credentials", ".mplcache") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path $_ | Out-Null
}
Ok "디렉터리 준비"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Ok ".env 생성 — API 키를 입력하세요!"
    Warn "  notepad .env"
} else {
    Ok ".env 이미 존재 (유지)"
}

# ── 5. Java JAR 빌드 ──
if ((Test-Path "supply-chain-agent") -and (Get-Command mvn -ErrorAction SilentlyContinue)) {
    Step "Supply Chain Agent JAR 빌드"
    Push-Location supply-chain-agent
    try {
        mvn -q package -DskipTests
        Ok "JAR 빌드 완료"
    } catch {
        Warn "JAR 빌드 실패 — 실행 시 Maven으로 자동 빌드됩니다"
    }
    Pop-Location
} else {
    Warn "Maven/supply-chain-agent 없음 — 밸류체인 분석 백엔드 스킵"
}

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host "  설치 완료!" -ForegroundColor Green
Write-Host ""
Write-Host "  1) API 키 설정:  notepad .env"
Write-Host "  2) 실행:  Start JARVIS.bat 더블클릭"
Write-Host "     또는:  .\start.bat"
Write-Host ""
Write-Host "  (선택) Windows 자격 증명 저장:  .venv\Scripts\python secure_config.py --migrate"
Write-Host "  (선택) 얼굴 인식: CMake+VS Build Tools 후 pip install face-recognition"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host ""
