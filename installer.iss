; Inno Setup 6 Script for Stacky (灵叠)
#define MyAppName "Stacky 灵叠"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "Stacky Desktop Labs"
#define MyAppExeName "BigFolderLauncher.exe"
#define SourceAppDir "dist\BigFolderLauncher"

[Setup]
AppId={{8B3654E7-B647-4E9A-A37F-C8659101BC4F}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={code:GetInstallDir}
UsePreviousAppDir=yes
UsePreviousTasks=yes
DirExistsWarning=no
EnableDirDoesntExistWarning=no
DisableDirPage=auto
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=Stacky-灵叠-安装程序
SetupIconFile=.cache_icons\app_icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
CloseApplicationsFilter=*.exe
RestartApplications=no
UninstallDisplayIcon={app}\.cache_icons\app_icon.ico
UninstallDisplayName={#MyAppName}
UpdateUninstallLogAppName=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "开机自动启动"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; 1. 保留用户个人配置与数据：更新/覆盖安装时绝对不覆盖现有 config.json！
Source: "{#SourceAppDir}\config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
; 2. 覆盖更新程序二进制与运行时组件
;    VC++ 运行库单独处理：常被其它进程映射占用，覆盖时 DeleteFile 报「错误代码 5 拒绝访问」。
;    这两个 DLL 各版本内容一致，已存在就跳过，全新安装才写入。
Source: "{#SourceAppDir}\*"; DestDir: "{app}"; Excludes: "config.json,VCRUNTIME140.dll,VCRUNTIME140_1.dll"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceAppDir}\_internal\VCRUNTIME140.dll"; DestDir: "{app}\_internal"; Flags: onlyifdoesntexist
Source: "{#SourceAppDir}\_internal\VCRUNTIME140_1.dll"; DestDir: "{app}\_internal"; Flags: onlyifdoesntexist

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--activate"; IconFilename: "{app}\.cache_icons\app_icon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--activate"; IconFilename: "{app}\.cache_icons\app_icon.ico"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Stacky"; ValueData: """{app}\{#MyAppExeName}"" --autostart"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
function GetInstallDir(Param: String): String;
var
  PrevDir: String;
begin
  // 1. 优先从当前用户卸载注册表中读取先前已选择的安装路径
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8B3654E7-B647-4E9A-A37F-C8659101BC4F}_is1', 'InstallLocation', PrevDir) and (PrevDir <> '') then
  begin
    Result := RemoveQuotes(PrevDir);
    Exit;
  end;
  // 2. 从本地计算机注册表读取
  if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8B3654E7-B647-4E9A-A37F-C8659101BC4F}_is1', 'InstallLocation', PrevDir) and (PrevDir <> '') then
  begin
    Result := RemoveQuotes(PrevDir);
    Exit;
  end;
  // 3. 从 32位兼容注册表读取
  if RegQueryStringValue(HKLM, 'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{8B3654E7-B647-4E9A-A37F-C8659101BC4F}_is1', 'InstallLocation', PrevDir) and (PrevDir <> '') then
  begin
    Result := RemoveQuotes(PrevDir);
    Exit;
  end;
  // 4. 自动检测历史已有安装目录（如用户曾安装在 D:\BigFolderLauncher）
  if DirExists('D:\BigFolderLauncher') then
  begin
    Result := 'D:\BigFolderLauncher';
    Exit;
  end;
  // 5. 首次全新安装默认路径
  Result := ExpandConstant('{localappdata}\Programs\BigFolderLauncher');
end;
