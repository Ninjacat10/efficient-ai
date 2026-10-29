; Inno Setup script for EfficientAI-Setup.exe - run packaging\build.ps1 rather than calling this directly.
; Installs per user (no admin prompt), adds Start Menu / Desktop shortcuts, the 3 AM task and start-at-login,
; and removes all of it again on uninstall. Your knowledge folder (%APPDATA%\EfficientAI) is left alone.

#define AppName "Efficient AI"
#define AppVersion "0.2.0"
#define AppExe "EfficientAI.exe"

[Setup]
AppId={{6E7C1B9A-2F4D-4C1E-9B3A-EFF1C1E7A1A1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Efficient AI (open source)
AppPublisherURL=https://github.com/Ninjacat10/efficient-ai
DefaultDirName={localappdata}\Programs\Efficient AI
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=EfficientAI-Setup-{#AppVersion}
SetupIconFile=..\assets\efficient.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start Efficient AI when I log in, and learn nightly at 3 AM"; GroupDescription: "Background:"

[Files]
Source: "..\dist\EfficientAI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "run"; AppUserModelID: "EfficientAI.App"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "run"; AppUserModelID: "EfficientAI.App"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "setup"; Flags: runhidden; Tasks: autostart; StatusMsg: "Setting up the nightly run..."
Filename: "{app}\{#AppExe}"; Parameters: "run"; Description: "Open Efficient AI now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "uninstall"; Flags: runhidden; RunOnceId: "RemoveTaskAndAutostart"
Filename: "taskkill"; Parameters: "/f /im {#AppExe}"; Flags: runhidden; RunOnceId: "StopApp"
