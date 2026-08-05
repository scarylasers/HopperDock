; HopperDock installer — build with:  ISCC.exe installer.iss
; (Inno Setup 6, https://jrsoftware.org/isdl.php)
;
; Deliberately a PER-USER install: it lands in %LOCALAPPDATA%\Programs, needs
; no administrator rights, and so never shows a UAC prompt. HopperDock is a
; personal dock — there is nothing it needs from an all-users install, and
; requiring elevation on top of the unsigned-binary SmartScreen warning would
; be two scary dialogs instead of one.
;
; The .exe does NOT go in the config folder. Settings, shortcuts, layouts and
; the icon cache live in %USERPROFILE%\HopperDock so that upgrading (replacing
; the binary) and uninstalling can never take them with it.

; Where to pick the built exe up from. Override when the normal dist\ copy is
; locked by a running dock:  ISCC.exe /DSourceDir=dist-staging installer.iss
#ifndef SourceDir
  #define SourceDir "dist"
#endif

#define AppName        "HopperDock"
#define AppVersion     "1.6.0"
#define AppPublisher   "ScaryLasers"
#define AppURL         "https://github.com/scarylasers/HopperDock"
#define AppExeName     "HopperDock.exe"

[Setup]
; Identifies the app across upgrades — never change it, or an upgrade turns
; into a second parallel install.
AppId={{A6A1601F-2BCC-4ABA-B1FB-E2C370664353}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename=HopperDock-{#AppVersion}-Setup
SetupIconFile=hopper-dock square logo with background.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user: no admin rights, no UAC prompt.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
; Upgrading over a running dock: Inno watches this mutex, so it can offer to
; close HopperDock instead of failing to overwrite a locked file. Must stay in
; step with the CreateMutexW name in window_dock.pyw.
AppMutex=Local\HopperDockSingleInstance_v1
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startuprun"; Description: "Start {#AppName} when I log in"; GroupDescription: "Startup:"

[Files]
Source: "{#SourceDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{#AppName} Guide"; Filename: "https://scarylasers.github.io/HopperDock/"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startuprun

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The exe unpacks itself to a temp dir each launch; nothing else to sweep.
; %USERPROFILE%\HopperDock is intentionally left alone — see the note below.
Type: dirifempty; Name: "{app}"

[Messages]
; Say plainly that uninstalling keeps their setup, so nobody hesitates to
; upgrade for fear of losing a dock they spent an evening arranging.
ConfirmUninstall=Remove %1?%n%nYour settings, shortcuts and layouts in your HopperDock config folder will be kept.
