; Inno Setup script for Clawde. Built by packaging\build.ps1, which passes /DAppVersion.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E8C1F4A-2B7D-4E39-9A51-C1A3D0E7F2B8}
AppName=Clawde
AppVersion={#AppVersion}
AppVerName=Clawde {#AppVersion}
AppPublisher=James Grady
AppPublisherURL=https://github.com/JaymeAlann/clawde
AppSupportURL=https://github.com/JaymeAlann/clawde/issues
DefaultDirName={localappdata}\Programs\Clawde
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\build\installer
OutputBaseFilename=Clawde-Setup-{#AppVersion}
SetupIconFile=clawde.ico
UninstallDisplayIcon={app}\Clawde.exe
UninstallDisplayName=Clawde
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "startup"; Description: "Start Clawde when I sign in to Windows"
Name: "hooks"; Description: "Show Claude Code permission requests on Clawde (adds a hook to Claude Code's settings)"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\build\dist\Clawde\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; old builds' files, so an upgrade doesn't leave stale libraries behind
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\hook"

[Icons]
Name: "{userprograms}\Clawde"; Filename: "{app}\Clawde.exe"
Name: "{userdesktop}\Clawde"; Filename: "{app}\Clawde.exe"; Tasks: desktopicon
Name: "{userstartup}\Clawde"; Filename: "{app}\Clawde.exe"; Tasks: startup

[Run]
Filename: "{app}\Clawde.exe"; Parameters: "--install-hooks"; Tasks: hooks; Flags: runhidden waituntilterminated
Filename: "{app}\Clawde.exe"; Description: "Start Clawde now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/im Clawde.exe /f"; Flags: runhidden; RunOnceId: "StopClawde"
Filename: "{app}\Clawde.exe"; Parameters: "--uninstall-hooks"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveHooks"
