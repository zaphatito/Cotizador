; ---------------------------------------------
; Instalador para Sistema de Cotizaciones
; ---------------------------------------------
#define MyAppName    "Sistema de Cotizaciones"
#define MyAppExeName "SistemaCotizaciones.exe"


; === Versionado (lo sobrescribe release.ps1) ===
#define MyAppVersion "1.2.12"

; === Manifiesto público para el updater (RAW GitHub) ===
#define UpdateManifestUrl "https://raw.githubusercontent.com/zaphatito/Cotizador/main/config/cotizador.json"

; Rutas locales de build
#define ProjectRoot  "C:\Users\Samuel\OneDrive\Escritorio\Cotizador"
#define BuildDir     "C:\Users\Samuel\OneDrive\Escritorio\Cotizador\dist\SistemaCotizaciones"

[Setup]
AppId={{9C0761F5-6555-4FA3-ACF5-9E9F968C7A10}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=SistemaCotizacionesPerfumes
VersionInfoCompany=SistemaCotizacionesPerfumes
VersionInfoVersion={#MyAppVersion}

PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
UsePreviousPrivileges=no

; Instalar por usuario (para poder actualizar sin admin)
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}

OutputBaseFilename=Setup_SistemaCotizaciones_{#MyAppVersion}
OutputDir={#ProjectRoot}\Output
Compression=lzma
SolidCompression=yes

; Instalación NUEVA: mostrar todas las páginas
DisableWelcomePage=no
DisableDirPage=yes
DisableProgramGroupPage=no
WizardStyle=modern
UsePreviousAppDir=no

; Arquitecturas modernas
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Evita warning por escribir en {userdocs} con admin
UsedUserAreasWarning=no

; Upgrades amables
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Dirs]
; No borrar datos de usuario nunca en upgrades
Name: "{userdocs}\Cotizaciones\data";           Flags: uninsneveruninstall
Name: "{userdocs}\Cotizaciones\cotizaciones";   Flags: uninsneveruninstall
Name: "{userdocs}\Cotizaciones\logs";           Flags: uninsneveruninstall
; Backups de configuración (para upgrades)
Name: "{localappdata}\Cotizaciones\config_backups"; Flags: uninsneveruninstall

; DB vive en {app}\sqlModels
Name: "{app}\sqlModels"

[Files]
; Bundle de PyInstaller (EXCLUYE la DB para no sobreescribir en upgrades)
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "sqlModels\app.sqlite3"
Source: "{#ProjectRoot}\changelog.txt"; DestDir: "{app}"; Flags: ignoreversion
; Referencia (opcional)
Source: "{#ProjectRoot}\Utilidades\requirements.txt"; DestDir: "{app}\Utilidades"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "Cotizador.1"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; AppUserModelID: "Cotizador.1"
Name: "{group}\Carpeta de Logs"; Filename: "{cmd}"; Parameters: "/c start """" ""{userdocs}\Cotizaciones\logs"""

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Run]
; Solo ejecutar al finalizar si es instalación NUEVA (no reinstalación silenciosa)
Filename: "{app}\{#MyAppExeName}"; Description: "Ejecutar {#MyAppName}"; Flags: nowait postinstall; Check: IsFreshInstall

[Code]
#ifdef UNICODE
  #define A "W"
#else
  #define A "A"
#endif

const
  MY_ATTR_HIDDEN = $00000002;
  MY_ATTR_SYSTEM = $00000004;
  UNINST_KEY = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{9C0761F5-6555-4FA3-ACF5-9E9F968C7A10}_is1';

function SetFileAttributes(lpFileName: string; dwFileAttributes: LongWord): Boolean;
  external 'SetFileAttributes{#A}@kernel32.dll stdcall';

procedure ForceDir(const Path: string);
begin
  if not DirExists(Path) then CreateDir(Path);
end;

procedure AttribClearRecursive(const Path: string);
var Cmd, Params: string; RC: Integer;
begin
  if DirExists(Path) then
  begin
    Cmd := ExpandConstant('{cmd}');
    Params := '/c attrib -s -h -r "' + Path + '\*" /S /D';
    Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, RC);
  end;
end;

procedure DeleteTreeForce(const Path: string);
begin
  if DirExists(Path) then
  begin
    AttribClearRecursive(Path);
    DelTree(Path, True, True, True);
  end;
end;

function RegReadStrAnyView(const Root: Integer; const Key, Name: string; var Val: string): Boolean;
begin
  Result := RegQueryStringValue(Root, Key, Name, Val);
  if (not Result) and IsWin64 then
    Result := RegQueryStringValue(HKLM64, Key, Name, Val);
end;

function PosEx2(const SubStr, S: string; Offset: Integer): Integer;
var i, L: Integer;
begin
  Result := 0;
  L := Length(SubStr);
  if Offset < 1 then Offset := 1;
  for i := Offset to Length(S) - L + 1 do
    if Copy(S, i, L) = SubStr then begin Result := i; Exit; end;
end;

function TrimQuotes(const S: string): string;
begin
  Result := S;
  if (Length(Result) >= 2) and (Result[1] = '"') and (Result[Length(Result)] = '"') then
    Result := Copy(Result, 2, Length(Result)-2);
end;

function LoadStringFromFileSafe(const FileName: string; var S: string): Boolean;
var
  A: AnsiString;
begin
  if not FileExists(FileName) then
  begin
    Result := False;
    Exit;
  end;
  Result := LoadStringFromFile(FileName, A);
  if Result then
    S := String(A)
  else
    S := '';
end;

function JsonExtractStr(const Json, Key: string; var OutVal: string): Boolean;
var LJson, LKey: string; p, q, r: Integer;
begin
  LJson := LowerCase(Json);
  LKey := '"' + LowerCase(Key) + '"';
  p := Pos(LKey, LJson);
  if p = 0 then begin Result := False; Exit; end;
  q := PosEx2(':', LJson, p);
  if q = 0 then begin Result := False; Exit; end;
  q := PosEx2('"', LJson, q);
  if q = 0 then begin Result := False; Exit; end;
  r := PosEx2('"', LJson, q+1);
  if r = 0 then begin Result := False; Exit; end;
  OutVal := TrimQuotes(Copy(Json, q, r-q+1));
  Result := True;
end;

function JsonExtractBool(const Json, Key: string; var OutVal: Boolean): Boolean;
var LJson, LKey: string; p, q: Integer; seg: string;
begin
  LJson := LowerCase(Json);
  LKey := '"' + LowerCase(Key) + '"';
  p := Pos(LKey, LJson);
  if p = 0 then begin Result := False; Exit; end;
  q := PosEx2(':', LJson, p);
  if q = 0 then begin Result := False; Exit; end;
  seg := Trim(Copy(LJson, q+1, 12));
  if Pos('true', seg) = 1 then begin OutVal := True; Result := True; Exit; end;
  if Pos('false', seg) = 1 then begin OutVal := False; Result := True; Exit; end;
  Result := False;
end;

function BoolToJson(const B: Boolean): string;
begin
  if B then Result := 'true' else Result := 'false';
end;

function SanitizeFilePart(const S: string): string;
var i: Integer; c: Char;
begin
  Result := '';
  for i := 1 to Length(S) do
  begin
    c := S[i];
    if (c >= '0') and (c <= '9') then Result := Result + c else
    if (c >= 'A') and (c <= 'Z') then Result := Result + c else
    if (c >= 'a') and (c <= 'z') then Result := Result + c else
    if (c = '.') or (c = '-') or (c = '_') then Result := Result + c
    else Result := Result + '_';
  end;
end;

var
  HaveOldConfig: Boolean;
  OldCountry, OldListing: string;
  OldAllow: Boolean;
  PrevDir: string;
  PrevVersion: string;
  IsReinstall: Boolean;

  PaisPage: TWizardPage;
  cbPais: TNewComboBox;

  ListadoPage: TWizardPage;
  cbListado: TNewComboBox;

  StockPage: TWizardPage;
  chkNoStock: TNewCheckBox;

function TryGetPrevAppDir(): Boolean;
begin
  Result := RegReadStrAnyView(HKLM, UNINST_KEY, 'Inno Setup: App Path', PrevDir);
  if not Result then
    Result := RegReadStrAnyView(HKCU, UNINST_KEY, 'Inno Setup: App Path', PrevDir);
  if (not Result) and DirExists(ExpandConstant('{localappdata}\{#MyAppName}')) then
  begin
    PrevDir := ExpandConstant('{localappdata}\{#MyAppName}');
    Result := True;
  end;
end;



var
  MaintAction: Integer;

function IsSilentMode(): Boolean;
var
  Tail: string;
begin
  Tail := UpperCase(GetCmdTail);

  Result :=
    WizardSilent or
    (Pos('/SILENT', Tail) > 0) or
    (Pos('/VERYSILENT', Tail) > 0);
end;



function GetUninstallCmd(var Exe, Params: string): Boolean;
var S: string; p: Integer;
begin
  Result := RegReadStrAnyView(HKCU, UNINST_KEY, 'UninstallString', S);
  if not Result then
    Result := RegReadStrAnyView(HKLM, UNINST_KEY, 'UninstallString', S);

  if not Result then Exit;

  S := Trim(S);

  if (Length(S) > 0) and (S[1] = '"') then
  begin
    p := PosEx2('"', S, 2);
    if p = 0 then begin Result := False; Exit; end;
    Exe := Copy(S, 2, p-2);
    Params := Trim(Copy(S, p+1, 2048));
  end
  else
  begin
    p := Pos(' ', S);
    if p > 0 then
    begin
      Exe := Copy(S, 1, p-1);
      Params := Trim(Copy(S, p+1, 2048));
    end
    else
    begin
      Exe := S;
      Params := '';
    end;
  end;

  Result := FileExists(Exe);
end;

procedure RunUninstallNow();
var Exe, Params: string; RC: Integer;
begin
  if not GetUninstallCmd(Exe, Params) then
  begin
    MsgBox('No se encontró el desinstalador previo.', mbError, MB_OK);
    Exit;
  end;

  { Lanzar desinstalación NORMAL (no silent) para que el usuario vea progreso.
    (Tu uninstaller ya no mostrará opciones porque lo vamos a simplificar.) }
  Exec(Exe, Params, '', SW_SHOW, ewWaitUntilTerminated, RC);
end;

function ShowMaintenanceDialog(): Integer;
var
  F: TSetupForm;
  lbl: TNewStaticText;
  btnRepair, btnUninstall, btnCancel: TNewButton;
begin
  Result := 0;

  F := CreateCustomForm();
  F.Caption := '{#MyAppName}';
  F.ClientWidth := ScaleX(520);
  F.ClientHeight := ScaleY(170);
  F.Position := poScreenCenter;

  lbl := TNewStaticText.Create(F);
  lbl.Parent := F;
  lbl.Left := ScaleX(12);
  lbl.Top := ScaleY(12);
  lbl.Width := F.ClientWidth - ScaleX(24);
  lbl.Height := ScaleY(80);
  lbl.AutoSize := False;
  lbl.WordWrap := True;
  lbl.Caption :=
    'Ya existe una instalación de "' + '{#MyAppName}' + '".' + #13#10 +
    '¿Qué deseas hacer?' + #13#10#13#10 +
    '• Reparar/Actualizar: reinstala archivos sin tocar "' + '{userdocs}\Cotizaciones' + '".' + #13#10 +
    '• Desinstalar: elimina la app y la configuración (mantiene "' + '{userdocs}\Cotizaciones' + '").';

  btnRepair := TNewButton.Create(F);
  btnRepair.Parent := F;
  btnRepair.Caption := 'Reparar / Actualizar';
  btnRepair.Left := ScaleX(12);
  btnRepair.Top := F.ClientHeight - ScaleY(12) - ScaleY(28);
  btnRepair.Width := ScaleX(160);
  btnRepair.ModalResult := mrOk;

  btnUninstall := TNewButton.Create(F);
  btnUninstall.Parent := F;
  btnUninstall.Caption := 'Desinstalar';
  btnUninstall.Left := btnRepair.Left + btnRepair.Width + ScaleX(10);
  btnUninstall.Top := btnRepair.Top;
  btnUninstall.Width := ScaleX(130);
  btnUninstall.ModalResult := mrYes;

  btnCancel := TNewButton.Create(F);
  btnCancel.Parent := F;
  btnCancel.Caption := 'Cancelar';
  btnCancel.Left := F.ClientWidth - ScaleX(12) - ScaleX(120);
  btnCancel.Top := btnRepair.Top;
  btnCancel.Width := ScaleX(120);
  btnCancel.ModalResult := mrCancel;

  case F.ShowModal() of
    mrOk:   Result := 1;  { Repair }
    mrYes:  Result := 2;  { Uninstall }
  else
    Result := 0;          { Cancel }
  end;
end;


procedure BackupPreviousConfig();
var
  PrevCfg, J: string;
  BackupDir, Tag, FullPath, MiniPath, LastPath: string;
  AllowStr: string;
  C, L: string;
  A: Boolean;
begin
  if (not IsReinstall) or (PrevDir = '') then Exit;

  PrevCfg := PrevDir + '\config\config.json';
  J := '';
  if not LoadStringFromFileSafe(PrevCfg, J) then Exit;

  BackupDir := ExpandConstant('{localappdata}\Cotizaciones\config_backups');
  ForceDir(BackupDir);

  Tag := '';
  if PrevVersion <> '' then
    Tag := 'v' + SanitizeFilePart(PrevVersion) + '_';
  Tag := Tag + GetDateTimeString('yyyymmdd_hhnnss', '', '');

  FullPath := BackupDir + '\config_full_' + Tag + '.json';
  SaveStringToFile(FullPath, J, False);

  LastPath := BackupDir + '\config_full_last.json';
  SaveStringToFile(LastPath, J, False);

  C := OldCountry; L := OldListing; A := OldAllow;

  if C = '' then JsonExtractStr(J, 'country', C);
  if L = '' then JsonExtractStr(J, 'listing_type', L);
  if not JsonExtractBool(J, 'allow_no_stock', A) then A := False;

  AllowStr := BoolToJson(A);

  MiniPath := BackupDir + '\config_settings_' + Tag + '.json';
  SaveStringToFile(
    MiniPath,
    '{' + #13#10 +
    '  "country": "' + UpperCase(C) + '",' + #13#10 +
    '  "listing_type": "' + UpperCase(L) + '",' + #13#10 +
    '  "allow_no_stock": ' + AllowStr + #13#10 +
    '}',
    False
  );
end;

function InitializeSetup(): Boolean;
var PrevCfg, J: string;
begin
  HaveOldConfig := False;
  OldCountry := '';
  OldListing := '';
  OldAllow := False;
  PrevDir := '';
  PrevVersion := '';

  IsReinstall := TryGetPrevAppDir();

  if IsReinstall then
  begin
    if not RegReadStrAnyView(HKLM, UNINST_KEY, 'DisplayVersion', PrevVersion) then
      RegReadStrAnyView(HKCU, UNINST_KEY, 'DisplayVersion', PrevVersion);

    PrevCfg := PrevDir + '\config\config.json';
    if FileExists(PrevCfg) and LoadStringFromFileSafe(PrevCfg, J) then
    begin
      if JsonExtractStr(J, 'country', OldCountry) then ;
      if JsonExtractStr(J, 'listing_type', OldListing) then ;
      if not JsonExtractBool(J, 'allow_no_stock', OldAllow) then OldAllow := False;
      HaveOldConfig := (Length(OldCountry) > 0) and (Length(OldListing) > 0);
    end;
  end;


  if IsReinstall and (not IsSilentMode()) then
  begin
    MaintAction := ShowMaintenanceDialog();

    if MaintAction = 2 then
    begin
      RunUninstallNow();
      Result := False;
      Exit;
    end;

    if MaintAction = 0 then
    begin
      Result := False;
      Exit;
    end;
  end;




  Result := True;
end;

function IsFreshInstall(): Boolean;
begin
  Result := not IsReinstall;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  if IsReinstall then
  begin
    if (PageID = wpInstalling) or (PageID = wpFinished) then
      Result := False
    else
      Result := True;
  end
  else
    Result := False;
end;

procedure InitializeWizard;
begin
  if IsReinstall then
    exit;

  PaisPage := CreateCustomPage(
    wpSelectDir,
    'País por defecto',
    'Elija el país con el que operará el sistema (afecta moneda y reglas de cantidad).'
  );
  cbPais := TNewComboBox.Create(PaisPage.Surface);
  cbPais.Parent := PaisPage.Surface;
  cbPais.Left := ScaleX(0);
  cbPais.Top := ScaleY(8);
  cbPais.Width := PaisPage.SurfaceWidth;
  cbPais.Style := csDropDownList;
  cbPais.Items.Add('Paraguay');
  cbPais.Items.Add('Perú');
  cbPais.Items.Add('Venezuela');
  cbPais.ItemIndex := 0;

  ListadoPage := CreateCustomPage(
    PaisPage.ID,
    'Tipo de listado',
    'Elija qué tipo de ítems mostrará el listado/autocompletar.'
  );
  cbListado := TNewComboBox.Create(ListadoPage.Surface);
  cbListado.Parent := ListadoPage.Surface;
  cbListado.Left := ScaleX(0);
  cbListado.Top := ScaleY(8);
  cbListado.Width := ListadoPage.SurfaceWidth;
  cbListado.Style := csDropDownList;
  cbListado.Items.Add('Productos');
  cbListado.Items.Add('Presentaciones');
  cbListado.Items.Add('Ambos');
  cbListado.ItemIndex := 2;

  StockPage := CreateCustomPage(
    ListadoPage.ID,
    'Permitir sin stock',
    'Puede permitir listar y cotizar productos/presentaciones sin stock disponible.'
  );
  chkNoStock := TNewCheckBox.Create(StockPage.Surface);
  chkNoStock.Parent := StockPage.Surface;
  chkNoStock.Caption := 'Permitir listar y cotizar sin stock';
  chkNoStock.Left := ScaleX(0);
  chkNoStock.Top := ScaleY(8);
  chkNoStock.Width := StockPage.SurfaceWidth;
  chkNoStock.Checked := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  PaisSel, ListadoSelUpper, AllowStr: string;
  ConfigFolder, FJson, ConfJson, OldConfigPath: string;
begin
  try
    if CurStep = ssInstall then
    begin
      if IsReinstall and (PrevDir <> '') then
      begin
        BackupPreviousConfig();
        OldConfigPath := PrevDir + '\config';
        if DirExists(OldConfigPath) then
          DeleteTreeForce(OldConfigPath);
      end;
    end;

    if CurStep = ssPostInstall then
    begin
      if HaveOldConfig then
      begin
        PaisSel := UpperCase(OldCountry);
        ListadoSelUpper := UpperCase(OldListing);
        AllowStr := BoolToJson(OldAllow);
      end
      else
      begin
        if not IsReinstall then
        begin
          case cbPais.ItemIndex of
            1: PaisSel := 'PERU';
            2: PaisSel := 'VENEZUELA';
          else
            PaisSel := 'PARAGUAY';
          end;

          case cbListado.ItemIndex of
            0: ListadoSelUpper := 'PRODUCTOS';
            1: ListadoSelUpper := 'PRESENTACIONES';
          else
            ListadoSelUpper := 'AMBOS';
          end;

          AllowStr := BoolToJson(chkNoStock.Checked);
        end
        else
        begin
          PaisSel := 'PARAGUAY';
          ListadoSelUpper := 'AMBOS';
          AllowStr := 'false';
        end;
      end;

      ConfigFolder := ExpandConstant('{app}\config');
      ForceDir(ConfigFolder);

      FJson := ConfigFolder + '\config.json';
      ConfJson :=
        '{' + #13#10 +
        '  "country": "' + PaisSel + '",' + #13#10 +
        '  "listing_type": "' + ListadoSelUpper + '",' + #13#10 +
        '  "allow_no_stock": ' + AllowStr + ',' + #13#10 +
        '  "update_mode": "SILENT",' + #13#10 +
        '  "update_check_on_startup": true,' + #13#10 +
        '  "update_manifest_url": "' + '{#UpdateManifestUrl}' + '",' + #13#10 +
        '  "update_apply_exe": "updater\\apply_update.exe",' + #13#10 +
        '  "update_ignore_paths": ["sqlModels/app.sqlite3"],' + #13#10 +
        '  "update_flags": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"' + #13#10 +
        '}';

      if not SaveStringToFile(FJson, ConfJson, False) then
        Log('WARN: No se pudo crear config.json en ' + FJson);
    end;

  except
    Log('ERROR: CurStepChanged exception: ' + GetExceptionMessage);
    { No relanzar: no aborta el setup}
  end;
end;



function InitializeUninstall(): Boolean;
begin
  { Sin opciones. Solo prepara atributos para borrar. }
  AttribClearRecursive(ExpandConstant('{app}'));
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  CfgPath: string;
  BackupDir: string;
  UpdaterDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { 1) SIEMPRE borrar config de la app }
    CfgPath := ExpandConstant('{app}\config');
    DeleteTreeForce(CfgPath);

    { 2) SIEMPRE borrar backups (localappdata) }
    BackupDir := ExpandConstant('{localappdata}\Cotizaciones\config_backups');
    DeleteTreeForce(BackupDir);

    { 3) SIEMPRE borrar logs/estado del updater (incluye apply_update.log) }
    UpdaterDir := ExpandConstant('{app}\updater');
    DeleteTreeForce(UpdaterDir);

  end;
end;












