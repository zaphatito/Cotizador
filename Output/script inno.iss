; ---------------------------------------------
; Instalador para Sistema de Cotizaciones
; ---------------------------------------------
#define MyAppName    "Sistema de Cotizaciones"
#define MyAppExeName "SistemaCotizaciones.exe"

; === Versionado (el release.ps1 lo sobrescribe) ===
#define MyAppVersion "2.2.2"

; === Manifiesto público para el updater ===
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
; *** minúsculas para coincidir con el repo ***
OutputDir={#ProjectRoot}\output
Compression=lzma
SolidCompression=yes
DisableDirPage=no
DisableProgramGroupPage=no
ArchitecturesInstallIn64BitMode=x64
; ==== Upgrades amables ====
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Dirs]
; Carpeta de configuración dentro del programa
Name: "{app}\config"
; Carpetas en Documentos del usuario (persisten entre desinstalaciones)
Name: "{userdocs}\Cotizaciones\data";         Flags: uninsneveruninstall
Name: "{userdocs}\Cotizaciones\cotizaciones"; Flags: uninsneveruninstall
Name: "{userdocs}\Cotizaciones\logs";         Flags: uninsneveruninstall

[Files]
; Binarios generados por PyInstaller
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; (Opcional) Requisitos a modo referencia
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

var
  PaisPage: TWizardPage;
  cbPais: TNewComboBox;

  ListadoPage: TWizardPage;
  cbListado: TNewComboBox;

  StockPage: TWizardPage;
  chkNoStock: TNewCheckBox;

procedure InitializeWizard;
begin
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

  { NOTA: No existe página de logging ni log_dir. Se deja por defecto INFO en código. }
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  PaisSel, ListadoSelUpper, AllowStr: string;
  FJson, ConfJson, Cmd, Params: string;
  ResultCode: Integer;
  ConfigFolder: string;
begin
  if CurStep = ssPostInstall then
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

    if chkNoStock.Checked then
      AllowStr := 'true'
    else
      AllowStr := 'false';

    { === Asegurar carpeta y escribir JSON === }
    ConfigFolder := ExpandConstant('{app}\config');
    ForceDir(ConfigFolder);

    FJson := ConfigFolder + '\app_config.json';
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
      MsgBox('No se pudo crear app_config.json en ' + FJson, mbError, MB_OK);

    { === Ocultar carpeta y archivo === }
    SetFileAttributes(ConfigFolder, MY_ATTR_HIDDEN or MY_ATTR_SYSTEM);
    SetFileAttributes(FJson, MY_ATTR_HIDDEN or MY_ATTR_SYSTEM);

    { === ACL: SYSTEM/Administrators Full, Users Read-Only === }
    Cmd := ExpandConstant('{cmd}');
    Params := '/c icacls "' + FJson + '" /inheritance:r /grant:r "SYSTEM":F "Administrators":F "Users":R';
    Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;



