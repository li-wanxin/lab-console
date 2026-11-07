; Lab Console v0.0.2 安装包脚本（Inno Setup 6）
; 编译: ISCC.exe setup.iss  ->  Output\LabConsole-Setup-v1.0.exe

#define MyAppName "Lab Console"
#define MyAppNameZh "实验室仪器控制台"
#define MyAppVersion "0.0.7"
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
VersionInfoVersion=0.0.7.0
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion=0.0.7.0

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

[Code]
// 清理历次"应用内更新"下载到临时目录的旧安装包。
// 用 [Code] 而非 [InstallDelete]: 正在运行的安装器自身匹配通配符、被 Windows
// 锁定, [InstallDelete] 遇占用可能弹错; DeleteFile 失败只返回 False, 静默跳过。
procedure CleanTempInstallers;
var
  Temp: String;
  Masks: array[0..1] of String;
  Res: TFindRec;
  I: Integer;
begin
  Temp := AddBackslash(GetEnv('TEMP'));
  Masks[0] := Temp + 'LabConsole-Setup-*.exe';
  Masks[1] := Temp + 'LabConsole-Portable*.exe';
  for I := 0 to 1 do
    if FindFirst(Masks[I], Res) then
    try
      repeat
        DeleteFile(AddBackslash(Temp) + Res.Name);
      until not FindNext(Res);
    finally
      FindClose(Res);
    end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    CleanTempInstallers;
end;

// ---- 安装前兜底检测目标 exe 是否被占用 ----
// AppMutex 只能检测 v0.0.6+ 的程序 (旧版本不创建互斥量, 无窗口僵尸进程也查不到)。
// 这里直接以独占方式打开 {app}\LabConsole.exe 试锁: 打不开说明在运行/残留,
// 提示用户先关闭再重试, 避免走到覆盖文件一步才报"拒绝访问"。
const
  GENERIC_WRITE = $40000000;
  OPEN_EXISTING = 3;
  INVALID_HANDLE_VALUE = $FFFFFFFF;
  CRLF = #13#10;

function CreateFileW(lpFileName: String; dwDesiredAccess, dwShareMode,
  lpSecurityAttributes, dwCreationDisposition, dwFlagsAndAttributes,
  hTemplateFile: Cardinal): Cardinal;
  external 'CreateFileW@kernel32.dll stdcall';

function CloseHandle(hObject: Cardinal): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function IsFileLocked(const Path: String): Boolean;
var
  H: Cardinal;
begin
  H := CreateFileW(Path, GENERIC_WRITE, 0, 0, OPEN_EXISTING, 0, 0);
  if H = INVALID_HANDLE_VALUE then
    Result := True
  else begin
    CloseHandle(H);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExePath, Msg: String;
  Ret: Integer;
begin
  Result := '';
  ExePath := ExpandConstant('{app}\{#MyAppExeName}');
  while FileExists(ExePath) and IsFileLocked(ExePath) do
  begin
    Msg := '检测到 Lab Console 正在运行（或进程残留），无法覆盖程序文件。' + CRLF + CRLF +
      '请先退出 Lab Console（必要时在任务管理器结束 LabConsole.exe），然后点击"重试"继续安装。';
    Ret := MsgBox(Msg, mbError, MB_RETRYCANCEL);
    if Ret = IDCANCEL then
    begin
      Result := 'Lab Console 正在运行，已取消安装。请关闭程序后重新运行安装包。';
      exit;
    end;
  end;
end;

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Messages]
; AppMutex 检测到程序在运行时的提示, 默认是英文, 覆盖为中文
SetupAppRunningError=安装程序检测到 %1 正在运行。%n%n请先关闭它的所有窗口（必要时在任务管理器结束 LabConsole.exe），然后点击"确定"继续安装，或点击"取消"退出。
UninstallAppRunningError=卸载程序检测到 %1 正在运行。%n%n请先关闭它的所有窗口，然后点击"确定"继续，或点击"取消"退出。
