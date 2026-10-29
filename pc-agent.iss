; Inno Setup script for pc-agent. Builds a per-user installer (no admin needed) that installs the
; three exes, makes Start-Menu + desktop shortcuts, and points the user at Ollama if it's missing.
; Compile with:  ISCC.exe pc-agent.iss   ->  installer\pc-agent-setup.exe

#define MyAppName "pc-agent"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "pc-agent"
#define BuilderExe "pc-agent-builder.exe"
#define AgentExe "pc-agent.exe"
#define CoreExe "pc-agent-core.exe"

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
OutputBaseFilename=pc-agent-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#BuilderExe}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut for the Builder"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\{#BuilderExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\{#AgentExe}";   DestDir: "{app}"; Flags: ignoreversion
Source: "dist\{#CoreExe}";    DestDir: "{app}"; Flags: ignoreversion
Source: "icon.ico";           DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";          DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\pc-agent Builder";        Filename: "{app}\{#BuilderExe}"
Name: "{group}\pc-agent (chat)";         Filename: "{app}\{#AgentExe}"
Name: "{group}\Uninstall pc-agent";      Filename: "{uninstallexe}"
Name: "{autodesktop}\pc-agent Builder";  Filename: "{app}\{#BuilderExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#BuilderExe}"; Description: "Open the pc-agent Builder now"; Flags: nowait postinstall skipifsilent

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
      if MsgBox('pc-agent runs AI models locally through Ollama, which does not appear to be installed.'
         + #13#10 + #13#10 + 'Open the Ollama download page now? (Install it, then open the pc-agent Builder'
         + ' to download a model.)', mbConfirmation, MB_YESNO) = IDYES then
        ShellExec('open', 'https://ollama.com/download', '', '', SW_SHOW, ewNoWait, ResultCode);
    end;
  end;
end;
