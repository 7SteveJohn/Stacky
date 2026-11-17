; Inno Setup 6 Script for Stacky (灵叠)
#define MyAppName "Stacky 灵叠"
#define MyAppVersion "1.0.3"
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
CloseApplicationsFilter=*.exe,*.dll,*.pyd
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
; 2. 完整覆盖更新程序二进制及 PySide6/VC++ 运行时组件（含只读属性强制覆盖与卸载清理）
Source: "{#SourceAppDir}\*"; DestDir: "{app}"; Excludes: "config.json,*.old_locked_*"; Flags: ignoreversion recursesubdirs createallsubdirs overwritereadonly uninsremovereadonly

[UninstallDelete]
Type: files; Name: "{app}\*.old_locked_*"
Type: files; Name: "{app}\_internal\*.old_locked_*"
Type: files; Name: "{app}\_internal\PySide6\*.old_locked_*"

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

function IsFileExclusivelyWritable(const FileName: String): Boolean;
var
  FS: TFileStream;
begin
  Result := True;
  if not FileExists(FileName) then
    Exit;
  try
    FS := TFileStream.Create(FileName, fmOpenReadWrite or fmShareExclusive);
    FS.Free;
  except
    Result := False;
  end;
end;

procedure PurgeAndSidestepLockedBinaries(const DirPath: String);
var
  FindRec: TFindRec;
  FullPath, LowerName, Ext, Candidate: String;
  Idx: Integer;
begin
  if not DirExists(DirPath) then
    Exit;
  if FindFirst(AddBackslash(DirPath) + '*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          FullPath := AddBackslash(DirPath) + FindRec.Name;
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          begin
            PurgeAndSidestepLockedBinaries(FullPath);
          end
          else
          begin
            LowerName := Lowercase(FindRec.Name);
            if Pos('.old_locked_', LowerName) > 0 then
            begin
              // 自动清理历次升级遗留且已释放句柄的旧版副本
              DeleteFile(FullPath);
            end
            else
            begin
              Ext := Lowercase(ExtractFileExt(FindRec.Name));
              if (Ext = '.dll') or (Ext = '.pyd') or (Ext = '.exe') then
              begin
                if not IsFileExclusivelyWritable(FullPath) then
                begin
                  Idx := 1;
                  Candidate := FullPath + '.old_locked_' + IntToStr(Idx);
                  while FileExists(Candidate) and (Idx < 100) do
                  begin
                    if DeleteFile(Candidate) then
                      Break;
                    Idx := Idx + 1;
                    Candidate := FullPath + '.old_locked_' + IntToStr(Idx);
                  end;
                  if RenameFile(FullPath, Candidate) then
                    Log('Sidestepped third-party mapped binary via NTFS rename: ' + FullPath + ' -> ' + Candidate)
                  else
                    Log('Warning: Could not rename still-locked binary: ' + FullPath);
                end;
              end;
            end;
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  AppDir, AppExe, OpenGlDll: String;
  ResultCode, I: Integer;
  ExeUnlocked, GlUnlocked: Boolean;
begin
  Result := '';
  NeedsRestart := False;
  AppDir := ExpandConstant('{app}');
  if not DirExists(AppDir) then
    Exit;

  AppExe := AddBackslash(AppDir) + '{#MyAppExeName}';
  OpenGlDll := AddBackslash(AppDir) + '_internal\PySide6\opengl32sw.dll';

  // 1. 优先通过 IPC 发送优雅退出指令 (--quit)
  if FileExists(AppExe) then
  begin
    Exec(AppExe, '--quit', AppDir, SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  // 2. 强制终止残留进程树，确保无后台常驻句柄
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  // 3. 剥离安装目录下可能存在的只读属性，防止 DeleteFile 返回拒绝访问 (Error 5)
  Exec(ExpandConstant('{sys}\attrib.exe'), '-R "' + AppDir + '\*" /S /D', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  // 4. 轮询最多 25 次 (25 * 200ms = 5s)，等待 Windows 内核 MmFlushImageSection 完全释放映像节句柄
  for I := 1 to 25 do
  begin
    ExeUnlocked := IsFileExclusivelyWritable(AppExe);
    GlUnlocked := IsFileExclusivelyWritable(OpenGlDll);
    if ExeUnlocked and GlUnlocked then
      Break;
    if (I mod 5 = 0) or (not ExeUnlocked) then
    begin
      Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
    Sleep(200);
  end;

  // 5. 兜底无感热替换：若外部长生命周期进程（如模拟器/IDE）仍映射占用某个 DLL，在同卷 NTFS 原位改名避让，避免误杀第三方进程且 100% 根除 DeleteFile Error 5
  PurgeAndSidestepLockedBinaries(AppDir);
end;
