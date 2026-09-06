#ifndef AppVersion
  #error AppVersion is required
#endif
[Setup]
AppId={{386C17A1-F93C-451F-A530-168595214C91}
AppName=OBS Scene Switcher
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\OBS Scene Switcher
DefaultGroupName=OBS Scene Switcher
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=obs-scene-switcher-{#AppVersion}-windows-x64-setup
SetupIconFile=..\build\icon.ico
UninstallDisplayIcon={app}\obs-scene-switcher.exe
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
[Files]
Source: "..\dist\obs-scene-switcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\OBS Scene Switcher"; Filename: "{app}\obs-scene-switcher.exe"
Name: "{userdesktop}\OBS Scene Switcher"; Filename: "{app}\obs-scene-switcher.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\obs-scene-switcher.exe"; Description: "Launch OBS Scene Switcher"; Flags: nowait postinstall skipifsilent
