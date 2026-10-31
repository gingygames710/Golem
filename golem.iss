; Inno Setup script for Golem. Builds a per-user installer (no admin needed) that installs the
; three exes, makes Start-Menu + desktop shortcuts, and points the user at Ollama if it's missing.
; Compile with:  ISCC.exe golem.iss   ->  installer\Golem-setup-<version>.exe

#define MyAppName "Golem"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "Golem"
#define BuilderExe "Golem-Builder.exe"
#define AgentExe "Golem.exe"
#define CoreExe "golem-core.exe"
#define OrbExe "Golem-Orb.exe"

[Setup]
AppId={{B7E6F3A2-1C4D-4E8B-9A3F-2D5C6E7F8A90}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=Golem-setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; So an in-place update (from the Builder's "Update now") can replace the running exes cleanly.
CloseApplications=yes
RestartApplications=no
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#BuilderExe}

[Tasks]
Name: "desktopagent"; Description: "Create a desktop shortcut for Golem (the chat agent)"; GroupDescription: "Additional icons:"
Name: "desktopicon"; Description: "Create a desktop shortcut for the Builder"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\{#BuilderExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\{#AgentExe}";   DestDir: "{app}"; Flags: ignoreversion
Source: "dist\{#CoreExe}";    DestDir: "{app}"; Flags: ignoreversion
Source: "dist\{#OrbExe}";     DestDir: "{app}"; Flags: ignoreversion
Source: "icon.ico";           DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";          DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\Golem Builder";        Filename: "{app}\{#BuilderExe}"
Name: "{group}\Golem (chat)";         Filename: "{app}\{#AgentExe}"
Name: "{group}\Golem Orb";            Filename: "{app}\{#OrbExe}"
Name: "{group}\Uninstall Golem";      Filename: "{uninstallexe}"
Name: "{autodesktop}\Golem";          Filename: "{app}\{#AgentExe}"; Tasks: desktopagent
Name: "{autodesktop}\Golem Builder";  Filename: "{app}\{#BuilderExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#BuilderExe}"; Description: "Open the Golem Builder now"; Flags: nowait postinstall skipifsilent

[Code]
// When Ollama (the free local AI engine) isn't installed, Golem's own installer downloads and
// SILENTLY installs it for the user - no web page, no second wizard, no terminal. OllamaSetup.exe is
// an Inno Setup installer (it documents /DIR), so /VERYSILENT works. Falls back gracefully offline.
var
  DownloadPage: TDownloadWizardPage;
  OllamaWanted: Boolean;

function OllamaInstalled(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('cmd.exe', '/c where ollama', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage('Setting up Ollama',
    'Downloading the free local AI engine that Golem runs models on. No action needed.', nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID <> wpReady then
    Exit;
  OllamaWanted := False;
  if not OllamaInstalled() then
  begin
    if MsgBox('Golem needs Ollama - the free engine it runs AI models on - and it is not installed yet.'
       + #13#10 + #13#10 + 'Install it automatically now? (about 1 GB downloads; nothing for you to click.)'
       + #13#10 + 'Choose No to skip - you can set it up later from the Golem Builder.',
       mbConfirmation, MB_YESNO) = IDYES then
    begin
      DownloadPage.Clear;
      DownloadPage.Add('https://ollama.com/download/OllamaSetup.exe', 'OllamaSetup.exe', '');
      DownloadPage.Show;
      try
        try
          DownloadPage.Download;
          OllamaWanted := True;
        except
          MsgBox('Could not download Ollama automatically (no internet?). You can install it later from'
             + ' the Golem Builder - open it and click "Install for me" on the Brain tab.',
             mbInformation, MB_OK);
        end;
      finally
        DownloadPage.Hide;
      end;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if (CurStep = ssPostInstall) and OllamaWanted then
  begin
    if FileExists(ExpandConstant('{tmp}\OllamaSetup.exe')) then
      Exec(ExpandConstant('{tmp}\OllamaSetup.exe'), '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
           '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
  end;
end;
