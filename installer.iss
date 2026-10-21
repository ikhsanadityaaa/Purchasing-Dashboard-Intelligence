; Offline-first prerequisites for Serveone Dashboard Desktop V1.0.0
; Build: build_windows.bat (includes signed WebView2 Evergreen Standalone x64).
; The WebView2 runtime is sourced from Microsoft at BUILD time only.
#define MyAppName "Serveone Dashboard"
#define MyAppVersion "1.0.0"
#define MyAppExeName "ServeoneDashboard.exe"
#ifnexist "vendor\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
  #error "Missing OFFLINE WebView2 Runtime. Run build_installer_windows.bat."
#endif
[Setup]
AppId={{8B32921A-332D-475D-96E8-8EEC7CF47021}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\Serveone Dashboard
DefaultGroupName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=ServeoneDashboard-Setup-V1.0.0
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=assets\serveone_icon.ico
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes

[InstallDelete]
; Remove only packaged application payload. User data lives outside {app}.
Type: files; Name: "{app}\{#MyAppExeName}"
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\support"

[Files]
Source: "dist\ServeoneDashboard\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Cached signed Microsoft Runtime allows offline repair when missing after install.
Source: "vendor\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; DestDir: "{app}\support"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Serveone Dashboard"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Serveone Dashboard"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\Serveone Dashboard"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart
[Tasks]
Name: desktopicon; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"
Name: autostart; Description: "Start Serveone Dashboard when I sign in"; GroupDescription: "Additional options:"
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Serveone Dashboard"; Flags: nowait postinstall skipifsilent; Check: PrerequisitesOk

[Code]
var
  WebView2InstallationFailed: Boolean;

function VersionIsValid(const V: String): Boolean;
begin
  Result := (V <> '') and (V <> '0.0.0.0');
end;

function WebView2Installed(): Boolean;
var
  V: String;
  Key: String;
begin
  Result := False;
  Key := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  if RegQueryStringValue(HKCU, Key, 'pv', V) and VersionIsValid(V) then
  begin Result := True; Exit; end;
  if RegQueryStringValue(HKLM32, Key, 'pv', V) and VersionIsValid(V) then
  begin Result := True; Exit; end;
  if IsWin64 then
    if RegQueryStringValue(HKLM64, Key, 'pv', V) and VersionIsValid(V) then
    begin Result := True; Exit; end;
end;

function PrerequisitesOk(): Boolean;
begin
  Result := not WebView2InstallationFailed and WebView2Installed();
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  WebView2InstallationFailed := False;
  if not WebView2Installed() then
  begin
    Result := MsgBox(
      'Microsoft Edge WebView2 Runtime is required to display the Serveone Dashboard.' + #13#10 + #13#10 +
      'This Setup already includes the official Microsoft offline installer.' + #13#10 +
      'Install this missing component automatically before launching the dashboard?' + #13#10 + #13#10 +
      'No Python installation or separate download is required on this computer.',
      mbConfirmation, MB_YESNO) = IDYES;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  RC: Integer;
  Exe: String;
  Started: Boolean;
begin
  if CurStep <> ssPostInstall then Exit;
  if WebView2Installed() then
  begin
    Log('WebView2 Runtime already available. No prerequisite installation needed.');
    Exit;
  end;
  Log('WebView2 missing: installing bundled Microsoft Evergreen Standalone Runtime.');
  WizardForm.StatusLabel.Caption := 'Installing Microsoft WebView2 Runtime (offline)...';
  Exe := ExpandConstant('{app}\support\MicrosoftEdgeWebView2RuntimeInstallerX64.exe');
  RC := -1;
  Started := Exec(Exe, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, RC);
  if (not Started) or ((RC <> 0) and (RC <> 3010)) or (not WebView2Installed()) then
  begin
    WebView2InstallationFailed := True;
    Log('WebView2 Runtime installation failed; return code=' + IntToStr(RC));
    MsgBox(
      'Serveone Dashboard was installed, but Microsoft WebView2 Runtime could not be verified.' + #13#10 +
      'The dashboard will not launch automatically.' + #13#10 +
      'Contact your administrator or retry the component setup in the application.', mbError, MB_OK);
  end
  else
  begin
    Log('WebView2 Runtime installed and verified successfully.');
    WizardForm.StatusLabel.Caption := 'Microsoft WebView2 Runtime installed successfully.';
  end;
end;
