@echo off
setlocal EnableExtensions
cd /d "%~dp0"
rem Windows x64 build PC only; the client machines do NOT need Python or pip.
if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv
  if errorlevel 1 (echo ERROR: Python 3.11 x64 required on build PC. & exit /b 1)
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install -r requirements-windows.txt
if errorlevel 1 exit /b 1
rem Fail early if the SAP automation runtime is not bundled in the build environment.
python -c "import psutil, pyautogui, pygetwindow, pyperclip, pywinauto, win32com.client; print('Windows automation modules OK')"
if errorlevel 1 (echo ERROR: One or more Windows automation dependencies are missing. & exit /b 1)
python -m unittest discover -s tests -v
if errorlevel 1 (echo TESTS FAILED: build stopped. & exit /b 1)
rem Package frontend source through a clean staging folder so local databases,
rem OAuth files, and DPAPI secrets can never enter the executable.
set "BUILD_DASHBOARD=%TEMP%\ServeoneDashboard-build-%RANDOM%-%RANDOM%"
set "BUILD_AUTOMATION=%TEMP%\ServeoneAutomation-build-%RANDOM%-%RANDOM%"
powershell.exe -NoProfile -NonInteractive -Command "$src=(Resolve-Path 'dashboard').Path;$dst='%BUILD_DASHBOARD%';New-Item -ItemType Directory -Path $dst -Force|Out-Null;Get-ChildItem -LiteralPath $src -Force|Where-Object{$_.Name -notin @('instance','__pycache__')}|Copy-Item -Destination $dst -Recurse -Force;Get-ChildItem -LiteralPath $dst -Recurse -File|Where-Object{$_.Extension -in @('.db','.sqlite','.sqlite3','.dpapi') -or $_.Name -eq '.env' -or $_.Name -like 'client_secret*.json'}|Remove-Item -Force"
if errorlevel 1 (echo ERROR: Could not prepare clean dashboard build input. & exit /b 1)
powershell.exe -NoProfile -NonInteractive -Command "$src=(Resolve-Path 'automation').Path;$dst='%BUILD_AUTOMATION%';New-Item -ItemType Directory -Path $dst -Force|Out-Null;Get-ChildItem -LiteralPath $src -File -Filter '*.py'|Copy-Item -Destination $dst -Force"
if errorlevel 1 (echo ERROR: Could not prepare clean automation build input. & exit /b 1)
python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name ServeoneDashboard ^
  --icon "assets\serveone_icon.ico" ^
  --paths dashboard --paths "%BUILD_AUTOMATION%" ^
  --add-data "%BUILD_DASHBOARD%;dashboard" ^
  --add-data "%BUILD_AUTOMATION%;automation" ^
  --hidden-import app ^
  --hidden-import psutil ^
  --hidden-import pyautogui ^
  --hidden-import pygetwindow ^
  --hidden-import pyperclip ^
  --hidden-import pywinauto ^
  --hidden-import win32com.client ^
  --hidden-import pythoncom ^
  --hidden-import pywintypes ^
  --hidden-import google.oauth2.credentials ^
  --hidden-import google_auth_oauthlib.flow ^
  --hidden-import google.auth.transport.requests ^
  --hidden-import xlrd ^
  --hidden-import main ^
  --hidden-import automation_serp ^
  --hidden-import automation_pur_info_reg ^
  --hidden-import i18n ^
  --hidden-import net_tls ^
  --hidden-import _paths ^
  --hidden-import run_presence ^
  --hidden-import tkinter ^
  --collect-all webview ^
  --collect-all selenium ^
  --collect-all webdriver_manager ^
  desktop_launcher.py
if errorlevel 1 exit /b 1
powershell.exe -NoProfile -NonInteractive -Command "$tmp=[IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')+'\';foreach($raw in @('%BUILD_DASHBOARD%','%BUILD_AUTOMATION%')){$dst=[IO.Path]::GetFullPath($raw);if(-not $dst.StartsWith($tmp,[StringComparison]::OrdinalIgnoreCase)){exit 1};Remove-Item -LiteralPath $dst -Recurse -Force}"
echo.
echo Desktop exe: dist\ServeoneDashboard\ServeoneDashboard.exe
echo No database or account secret is included in the build.
if /I "%~1"=="--exe-only" (echo Skipping installer as requested. & exit /b 0)
set "ISCC_PATH="
where ISCC.exe >nul 2>&1
if not errorlevel 1 set "ISCC_PATH=ISCC.exe"
if not defined ISCC_PATH if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH (
  echo.
  echo WARNING: Inno Setup 6 not found. The exe was built, but Setup.exe WAS NOT created.
  echo Install Inno Setup 6 on this build PC, then run build_installer_windows.bat.
  exit /b 0
)
call build_installer_windows.bat
if errorlevel 1 (echo ERROR: Installer build failed. & exit /b 1)
endlocal
