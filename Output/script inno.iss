; ---------------------------------------------
; Instalador para Sistema de Cotizaciones
; ---------------------------------------------
#define MyAppName    "Sistema de Cotizaciones"
#define MyAppExeName "SistemaCotizaciones.exe"

; === Versionado (lo sobrescribe release.ps1) ===
#define MyAppVersion "1.1.5"

; === Manifiesto público para el updater (RAW GitHub) ===
#define UpdateManifestUrl "https://raw.githubusercontent.com/zaphatito/Cotizador/main/config/cotizador.json"

; Rutas locales de build
#define ProjectRoot  "C:\Users\Samuel\OneDrive\Escritorio\Cotizador"
#define BuildDir     "C:\Users\Samuel\OneDrive\Escritorio\Cotizador\dist\SistemaCotizaciones"

[Setup]
AppId={{9C0761F5-6555-4FA3-ACF5-9E9F968C7A10}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}

; Usar forma "auto" para Program Files
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}

OutputBaseFilename=Setup_SistemaCotizaciones_{#MyAppVersion}
OutputDir={#ProjectRoot}\Output
Compression=lzma
SolidCompression=yes

; Instalación NUEVA: mostrar todas las páginas
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=no
WizardStyle=modern

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
Name: "{userdocs}\Cotizaciones\data";         Flags: uninsneveruninstall
Name: "{userdocs}\Cotizaciones\cotizaciones"; Flags: uninsneveruninstall
Name: "{userdocs}\Cotizaciones\logs";         Flags: uninsneveruninstall

[Files]
; Bundle de PyInstaller
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Referencia (opcional)
Source: "{#ProjectRoot}\Utilidades\requirements.txt"; DestDir: "{app}\Utilidades"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "Cotizador.1"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; AppUserModelID: "Cotizador.1"
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

var
  HaveOldConfig: Boolean;
  OldCountry, OldListing: string;
  OldAllow: Boolean;
  PrevDir: string;
  IsReinstall: Boolean;

  // Controles de las "3 ventanas viejas"
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
  if (not Result) and DirExists(ExpandConstant('{autopf}\') + '{#MyAppName}') then
  begin
    PrevDir := ExpandConstant('{autopf}\') + '{#MyAppName}';
    Result := True;
  end;
end;

function InitializeSetup(): Boolean;
var PrevCfg, J: string;
begin
  HaveOldConfig := False;
  OldCountry := '';
  OldListing := '';
  OldAllow := False;
  PrevDir := '';

  IsReinstall := TryGetPrevAppDir();

  if IsReinstall then
  begin
    PrevCfg := PrevDir + '\config\config.json';
    if FileExists(PrevCfg) and LoadStringFromFileSafe(PrevCfg, J) then
    begin
      if JsonExtractStr(J, 'country', OldCountry) then ;
      if JsonExtractStr(J, 'listing_type', OldListing) then ;
      if not JsonExtractBool(J, 'allow_no_stock', OldAllow) then OldAllow := False;
      HaveOldConfig := (Length(OldCountry) > 0) and (Length(OldListing) > 0);
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
  { En REINSTALACIÓN: ocultar todo excepto Instalando y Finalización }
  if IsReinstall then
  begin
    if (PageID = wpInstalling) or (PageID = wpFinished) then
      Result := False
    else
      Result := True;
  end
  else
    Result := False;  { Instalación nueva: no saltar páginas }
end;

procedure InitializeWizard;
begin
  if IsReinstall then
    exit;  { No crear páginas personalizadas en reinstalación }

  { === País === }
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

  { === Tipo de listado === }
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

  { === Permitir sin stock === }
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
  if CurStep = ssInstall then
  begin
    if IsReinstall and (PrevDir <> '') then
    begin
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
      if OldAllow then AllowStr := 'true' else AllowStr := 'false';
    end
    else
    begin
      if not IsReinstall then
      begin
        { Tomar de los controles de las 3 páginas }
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

        if chkNoStock.Checked then AllowStr := 'true' else AllowStr := 'false';
      end
      else
      begin
        { Reinstalación sin config previa detectable: defaults seguros }
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
      '  "update_mode": "ASK",' + #13#10 +
      '  "update_check_on_startup": true,' + #13#10 +
      '  "update_manifest_url": "' + '{#UpdateManifestUrl}' + '",' + #13#10 +
      '  "update_flags": "/CLOSEAPPLICATIONS"' + #13#10 +
      '}';

    if not SaveStringToFile(FJson, ConfJson, False) then
      MsgBox('No se pudo crear config.json en ' + FJson, mbError, MB_OK);

    SetFileAttributes(ConfigFolder, MY_ATTR_HIDDEN or MY_ATTR_SYSTEM);
    SetFileAttributes(FJson,        MY_ATTR_HIDDEN or MY_ATTR_SYSTEM);
    if FileExists(ConfigFolder + '\cotizador.json') then
      SetFileAttributes(ConfigFolder + '\cotizador.json', MY_ATTR_HIDDEN or MY_ATTR_SYSTEM);
  end;
end;

{ =========================
  === Desinstalador UI ===
  ========================= }

var
  UninstForm: TSetupForm;
  chkDelConfig, chkDelDocs: TNewCheckBox;
  btnOK, btnCancel: TNewButton;
  lblMsg: TNewStaticText;
  GDelConfig, GDelDocs: Boolean;

function ShowUninstallOptionsDialog(): Boolean;
begin
  if UninstallSilent then
  begin
    GDelConfig := False;
    GDelDocs := False;
    Result := True;
  end
  else
  begin
    Result := False;
    UninstForm := CreateCustomForm();
    UninstForm.Caption := 'Eliminar datos del usuario';
    UninstForm.ClientWidth := ScaleX(520);
    UninstForm.ClientHeight := ScaleY(180);
    UninstForm.Position := poScreenCenter;

    lblMsg := TNewStaticText.Create(UninstForm);
    lblMsg.Parent := UninstForm;
    lblMsg.Left := ScaleX(12);
    lblMsg.Top := ScaleY(12);
    lblMsg.Width := UninstForm.ClientWidth - ScaleX(24);
    lblMsg.AutoSize := False;
    lblMsg.WordWrap := True;
    lblMsg.Caption :=
      'Seleccione qué desea borrar además de los archivos instalados:' + #13#10 +
      '• Carpeta "config" de la aplicación (archivos de configuración).' + #13#10 +
      '• Carpeta "Documentos\Cotizaciones" (data, cotizaciones y logs).';

    chkDelConfig := TNewCheckBox.Create(UninstForm);
    chkDelConfig.Parent := UninstForm;
    chkDelConfig.Caption := 'Borrar carpeta de configuración (config) en {app}';
    chkDelConfig.Left := ScaleX(12);
    chkDelConfig.Top := lblMsg.Top + ScaleY(60);
    chkDelConfig.Width := UninstForm.ClientWidth - ScaleX(24);
    chkDelConfig.Checked := True;

    chkDelDocs := TNewCheckBox.Create(UninstForm);
    chkDelDocs.Parent := UninstForm;
    chkDelDocs.Caption := 'Borrar "{userdocs}\Cotizaciones" (incluye data, cotizaciones y logs)';
    chkDelDocs.Left := ScaleX(12);
    chkDelDocs.Top := chkDelConfig.Top + ScaleY(24);
    chkDelDocs.Width := UninstForm.ClientWidth - ScaleX(24);
    chkDelDocs.Checked := False;

    btnOK := TNewButton.Create(UninstForm);
    btnOK.Parent := UninstForm;
    btnOK.Caption := SetupMessage(msgButtonOK);
    btnOK.ModalResult := mrOk;
    btnOK.Left := UninstForm.ClientWidth - ScaleX(12) - ScaleX(200);
    btnOK.Top := UninstForm.ClientHeight - ScaleY(12) - ScaleY(23);
    btnOK.Width := ScaleX(100);

    btnCancel := TNewButton.Create(UninstForm);
    btnCancel.Parent := UninstForm;
    btnCancel.Caption := SetupMessage(msgButtonCancel);
    btnCancel.ModalResult := mrCancel;
    btnCancel.Left := UninstForm.ClientWidth - ScaleX(12) - ScaleX(100);
    btnCancel.Top := btnOK.Top;
    btnCancel.Width := ScaleX(100);

    if UninstForm.ShowModal() = mrOk then
    begin
      GDelConfig := chkDelConfig.Checked;
      GDelDocs := chkDelDocs.Checked;
      Result := True;
    end
    else
      Result := False;
  end;
end;

function InitializeUninstall(): Boolean;
var AppPath: string;
begin
  AppPath := ExpandConstant('{app}');
  AttribClearRecursive(AppPath);
  if not ShowUninstallOptionsDialog() then
  begin
    Result := False;
    Exit;
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var CfgPath, DocsPath: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if GDelConfig then
    begin
      CfgPath := ExpandConstant('{app}\config');
      DeleteTreeForce(CfgPath);
    end;
    if GDelDocs then
    begin
      DocsPath := ExpandConstant('{userdocs}\Cotizaciones');
      DeleteTreeForce(DocsPath);
    end;
    RemoveDir(ExpandConstant('{app}'));
  end;
end;





