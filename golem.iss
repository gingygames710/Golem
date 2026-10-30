; Inno Setup script for Golem. Builds a per-user installer (no admin needed) that installs the
; three exes, makes Start-Menu + desktop shortcuts, and points the user at Ollama if it's missing.
; Compile with:  ISCC.exe golem.iss   ->  installer\Golem-setup.exe

#define MyAppName "Golem"
#define MyAppVersion "0.7.0"
#define MyAppPublisher "Golem"
#define BuilderExe "Golem-Builder.exe"
#define AgentExe "Golem.exe"
#define CoreExe "golem-core.exe"

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
OutputBaseFilename=Golem-setup
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
Source: "icon.ico";           DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";          DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\Golem Builder";        Filename: "{app}\{#BuilderExe}"
Name: "{group}\Golem (chat)";         Filename: "{app}\{#AgentExe}"
Name: "{group}\Uninstall Golem";      Filename: "{uninstallexe}"
Name: "{autodesktop}\Golem";          Filename: "{app}\{#AgentExe}"; Tasks: desktopagent
Name: "{autodesktop}\Golem Builder";  Filename: "{app}\{#BuilderExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#BuilderExe}"; Description: "Open the Golem Builder now"; Flags: nowait postinstall skipifsilent

[Code]
function OllamaInstalled(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('cmd.exe', '/c where ollama', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if not OllamaInstalled() then
    begin
      if MsgBox('Golem runs AI models locally through Ollama, which does not appear to be installed.'
         + #13#10 + #13#10 + 'Open the Ollama download page now? (Install it, then open the Golem Builder'
         + ' to download a model.)', mbConfirmation, MB_YESNO) = IDYES then
        ShellExec('open', 'https://ollama.com/download', '', '', SW_SHOW, ewNoWait, ResultCode);
    end;
  end;
end;
