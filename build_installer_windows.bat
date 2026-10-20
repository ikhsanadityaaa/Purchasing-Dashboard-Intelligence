@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "dist\ServeoneDashboard\ServeoneDashboard.exe" (
  echo ERROR: Build app first with build_windows.bat --exe-only
  exit /b 1
)
rem WebView2 Runtime is downloaded on build PC, NOT on employee laptops.
rem If an approved offline copy already exists under vendor\, it is reused.
powershell.exe -NoProfile -NonInteractive -File "%~dp0prepare_webview2_runtime.ps1"
if errorlevel 1 (
  echo ERROR: Cannot obtain or verify signed Microsoft WebView2 Runtime.
  echo To build offline, place the OFFICIAL Microsoft x64 Evergreen Standalone
  echo Runtime at vendor\MicrosoftEdgeWebView2RuntimeInstallerX64.exe and rerun.
  exit /b 1
)
set "ISCC_PATH="
where ISCC.exe >nul 2>&1
if not errorlevel 1 set "ISCC_PATH=ISCC.exe"
if not defined ISCC_PATH if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH (echo ERROR: Inno Setup 6 compiler ISCC.exe not found. & exit /b 1)
"%ISCC_PATH%" "installer.iss"
if errorlevel 1 exit /b 1
echo.
echo Installer ready: installer_output\ServeoneDashboard-Desktop-Setup-V0.1.14.exe
echo This installer includes the OFFLINE signed Microsoft WebView2 Runtime.
endlocal
