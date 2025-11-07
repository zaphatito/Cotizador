; ---------------------------------------------
; Instalador para Sistema de Cotizaciones
; ---------------------------------------------
#define MyAppName    "Sistema de Cotizaciones"
#define MyAppExeName "SistemaCotizaciones.exe"

; === Versionado (lo sobrescribe release.ps1) ===
#define MyAppVersion "2.2.4"

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
DefaultDirName={pf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputBaseFilename=Setup_SistemaCotizaciones_{#MyAppVersion}
; === Usa 'Output' con O mayúscula para coincidir con el repo ===
OutputDir={#ProjectRoot}\Output
Compression=lzma
SolidCompression=yes
DisableDirPage=no
DisableProgramGroupPage=no
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Dirs]
; Carpetas de datos de usuario (por defecto se preservan).
Name: "{userdocs}\Cotizaciones\data";         Flags: uninsneveruninstall
Name: "{userdocs}\Cotizaciones\cotizaciones"; Flags: uninsneveruninstall
Name: "{userdocs}\Cotizaciones\logs";         Flags: uninsneveruninstall

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\Utilidades\requirements.txt"; DestDir: "{app}\Utilidades"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "Cotizador.1"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; AppUserModelID: "Cotizador.1"
Name: "{group}\Carpeta de Logs"; Filename: "{cmd}"; Parameters: "/c start """" ""{userdocs}\Cotizaciones\logs"""

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Ejecutar {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]

#ifdef UNICODE
  #define A "W"
#else
  #define A "A"
#endif

const
  MY_ATTR_HIDDEN = $00000002;
  MY_ATTR_SYSTEM = $00000004;

function SetFileAttributes(lpFileName: string; dwFileAttributes: LongWord): Boolean;
  external 'SetFileAttributes{#A}@kernel32.dll stdcall';

procedure ForceDir(const Path: string);
begin
  if not DirExists(Path) then
    CreateDir(Path);
end;

procedure AttribClearRecursive(const Path: string);
var
  Cmd, Params: string;
  RC: Integer;
begin
  if DirExists(Path) then
  begin
    Cmd := ExpandConstant('{cmd}');
    Params := '/c attrib -s -h -r "' + Path + '\*" /S /D';
    Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, RC);
    Log(Format('Atributos limpiados en: %s (RC=%d)', [Path, RC]));
  end;
end;

procedure DeleteTreeForce(const Path: string);
begin
  if DirExists(Path) then
  begin
    AttribClearRecursive(Path);
    if DelTree(Path, True, True, True) then
      Log(Format('Eliminada carpeta: %s', [Path]))
    else
      Log(Format('No se pudo eliminar carpeta: %s', [Path]));
  end;
end;

var
  ; ====== Páginas de instalación (país/listado/stock) ======
  PaisPage: TWizardPage;
  cbPais: TNewComboBox;

  ListadoPage: TWizardPage;
  cbListado: TNewComboBox;

  StockPage: TWizardPage;
  chkNoStock: TNewCheckBox;

  ; ====== Formulario del desinstalador (casillas para borrar) ======
  UninstForm: TSetupForm;
  chkDelConfig: TNewCheckBox;
  chkDelDocs: TNewCheckBox;
  btnOK, btnCancel: TNewButton;
  lblMsg: TNewStaticText;

  ; Flags elegidos por el usuario en desinstalación
  GDelConfig, GDelDocs: Boolean;

procedure InitializeWizard;
begin
  ; -------- Página País --------
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

  ; -------- Página Listado --------
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

  ; -------- Página Stock --------
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
  FJson, ConfJson, Cmd, Params: string;
  ResultCode: Integer;
  ConfigFolder: string;
  CotizadorPath: string;
begin
  if CurStep = ssPostInstall then
  begin
    case cbPais.ItemIndex of
      1: PaisSel := 'PERU';
      2: PaisSel := 'VENEZUELA';
      else PaisSel := 'PARAGUAY';
    end;

    case cbListado.ItemIndex of
      0: ListadoSelUpper := 'PRODUCTOS';
      1: ListadoSelUpper := 'PRESENTACIONES';
      else ListadoSelUpper := 'AMBOS';
    end;

    if chkNoStock.Checked then
      AllowStr := 'true'
    else
      AllowStr := 'false';

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

    ; Oculta y marca como sistema la carpeta/archivo de config
    SetFileAttributes(ConfigFolder, MY_ATTR_HIDDEN or MY_ATTR_SYSTEM);
    SetFileAttributes(FJson,        MY_ATTR_HIDDEN or MY_ATTR_SYSTEM);

    ; Si existe manifiesto de updater, también ocultarlo
    CotizadorPath := ConfigFolder + '\cotizador.json';
    if FileExists(CotizadorPath) then
      SetFileAttributes(CotizadorPath, MY_ATTR_HIDDEN or MY_ATTR_SYSTEM);

    ; Endurecer permisos mínimos (lectura para Users)
    Cmd := ExpandConstant('{cmd}');
    Params := '/c icacls "' + FJson + '" /inheritance:r /grant:r "SYSTEM":F "Administrators":F "Users":R';
    Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

; ========= Desinstalación =========

function ShowUninstallOptionsDialog(): Boolean;
var
  W, H, BtnW, BtnH, M, Sp: Integer;
begin
  Result := False;

  UninstForm := CreateCustomForm();
  UninstForm.Caption := 'Eliminar datos del usuario';
  W := ScaleX(520);
  H := ScaleY(180);
  BtnW := ScaleX(100);
  BtnH := ScaleY(23);
  M := ScaleX(12);
  Sp := ScaleY(10);

  UninstForm.ClientWidth := W;
  UninstForm.ClientHeight := H;
  UninstForm.Position := poScreenCenter;

  lblMsg := TNewStaticText.Create(UninstForm);
  lblMsg.Parent := UninstForm;
  lblMsg.Left := M;
  lblMsg.Top := M;
  lblMsg.Width := W - 2*M;
  lblMsg.AutoSize := False;
  lblMsg.WordWrap := True;
  lblMsg.Caption :=
    'Seleccione qué desea borrar además de los archivos instalados:' + #13#10 +
    '• Carpeta "config" de la aplicación (archivos de configuración ocultos).' + #13#10 +
    '• Carpeta "Documentos\\Cotizaciones" (data, cotizaciones y logs).';

  chkDelConfig := TNewCheckBox.Create(UninstForm);
  chkDelConfig.Parent := UninstForm;
  chkDelConfig.Caption := 'Borrar carpeta de configuración (config) en {app}';
  chkDelConfig.Left := M;
  chkDelConfig.Top := lblMsg.Top + ScaleY(60);
  chkDelConfig.Width := W - 2*M;
  chkDelConfig.Checked := True;  ; <-- por defecto, borrar config

  chkDelDocs := TNewCheckBox.Create(UninstForm);
  chkDelDocs.Parent := UninstForm;
  chkDelDocs.Caption := 'Borrar "{userdocs}\Cotizaciones" (incluye data, cotizaciones y logs)';
  chkDelDocs.Left := M;
  chkDelDocs.Top := chkDelConfig.Top + ScaleY(24);
  chkDelDocs.Width := W - 2*M;
  chkDelDocs.Checked := False;   ; <-- por defecto, conservar datos de usuario

  btnOK := TNewButton.Create(UninstForm);
  btnOK.Parent := UninstForm;
  btnOK.Caption := SetupMessage(msgButtonOK);
  btnOK.ModalResult := mrOk;
  btnOK.Left := W - M - BtnW*2 - ScaleX(8);
  btnOK.Top := H - M - BtnH;
  btnOK.Width := BtnW;
  btnOK.Height := BtnH;

  btnCancel := TNewButton.Create(UninstForm);
  btnCancel.Parent := UninstForm;
  btnCancel.Caption := SetupMessage(msgButtonCancel);
  btnCancel.ModalResult := mrCancel;
  btnCancel.Left := W - M - BtnW;
  btnCancel.Top := H - M - BtnH;
  btnCancel.Width := BtnW;
  btnCancel.Height := BtnH;

  if UninstForm.ShowModal() = mrOk then
  begin
    GDelConfig := chkDelConfig.Checked;
    GDelDocs := chkDelDocs.Checked;
    Result := True;
  end
  else
  begin
    Result := False;  ; usuario canceló -> abortar desinstalación
  end;
end;

function InitializeUninstall(): Boolean;
var
  AppPath: string;
begin
  ; Limpia atributos para que el uninstaller pueda borrar sin trabas
  AppPath := ExpandConstant('{app}');
  AttribClearRecursive(AppPath);

  ; Muestra diálogo de opciones (casillas)
  if not ShowUninstallOptionsDialog() then
  begin
    Result := False;  ; aborta desinstalación si cancela
    Exit;
  end;

  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  CfgPath, DocsPath: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    ; Borrar config si el usuario lo marcó
    if GDelConfig then
    begin
      CfgPath := ExpandConstant('{app}\config');
      DeleteTreeForce(CfgPath);
    end;

    ; Borrar Documentos\Cotizaciones si el usuario lo marcó
    if GDelDocs then
    begin
      DocsPath := ExpandConstant('{userdocs}\Cotizaciones');
      DeleteTreeForce(DocsPath);
    end;

    ; Intentar quitar la carpeta de la app si quedó vacía
    if RemoveDir(ExpandConstant('{app}')) then
      Log('Carpeta {app} eliminada (vacía).')
    else
      Log('Carpeta {app} no se pudo eliminar (puede contener archivos no controlados o estar en uso).');
  end;
end;
