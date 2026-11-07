; Lab Console v0.0.2 安装包脚本（Inno Setup 6）
; 编译: ISCC.exe setup.iss  ->  Output\LabConsole-Setup-v1.0.exe

#define MyAppName "Lab Console"
#define MyAppNameZh "实验室仪器控制台"
#define MyAppVersion "0.0.5"
#define MyAppPublisher "li-wanxin"
#define MyAppURL "https://github.com/li-wanxin/lab-console"
#define MyAppExeName "LabConsole.exe"

[Setup]
SetupIconFile=app.ico
AppId={{8E5C4B7A-2F3D-4E86-9C1B-A7D3F5E9C1B0}
AppName={#MyAppName} ({#MyAppNameZh})
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\LabConsole
DefaultGroupName={#MyAppName}
UninstallDisplayName={#MyAppName} v{#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputBaseFilename=LabConsole-Setup-v{#MyAppVersion}
OutputDir=Output
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
; 安装/卸载前检测程序是否在运行 (互斥量由 desktop.py 创建):
; 在安装开始时就提示用户先关闭, 而不是装到一半卡在"关闭应用程序"
AppMutex=LabConsoleAppMutex
; 程序已通过 AppMutex 保证先关闭, 跳过 Inno 的自动关闭应用阶段,
; 避免 pywebview 多进程 (WebView2) 导致该步骤卡住
CloseApplications=no
VersionInfoVersion=0.0.5.0
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion=0.0.5.0

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\LabConsole-Portable.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "DEVLOG.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
