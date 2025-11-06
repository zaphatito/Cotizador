; ---------------------------------------------
; Instalador para Sistema de Cotizaciones
; ---------------------------------------------
#define MyAppName    "Sistema de Cotizaciones"
#define MyAppExeName "SistemaCotizaciones.exe"

; === Versionado (lo sobreescribe el script de release) ===
#define MyAppVersion "2.2.4"

; === URL pÃºblica del manifiesto (RAW de GitHub). El script tambiÃ©n la puede reemplazar. ===
#define UpdateManifestUrl "https://raw.githubusercontent.com/zaphatito/Cotizador/main/config/cotizador.json"

; Rutas locales
#define ProjectRoot  "C:\Users\Samuel\OneDrive\Escritorio\Cotizador"
#define BuildDir     "C:\Users\Samuel\OneDrive\Escritorio\Cotizador\dist\SistemaCotizaciones"

[Setup]
AppId={{9C0761F5-6555-4FA3-ACF5-9E9F968C7A10}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={pf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; EXE versionado para subir a /output del repo
OutputBaseFilename=Setup_SistemaCotizaciones_{#MyAppVersion}
; Carpeta de salida en minÃºsculas (para que coincida con el repo)
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
Name: "{app}\config"
Name: "{userdocs}\Cotizaciones\data";         Flags: uninsneveruninstall
Name: "{userdocs}\Cotizaciones\cotizaciones"; Flags: uninsneveruninstall
Name: "{userdocs}\Cotizaciones\logs";         Flags: uninsneveruninstall

[Files]
; Binarios generados por PyInstaller
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Requisitos a modo referencia (el EXE ya los incluye)
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

var
  PaisPage: TWizardPage;
  cbPais: TNewComboBox;

  ListadoPage: TWizardPage;
  cbListado: TNewComboBox;

  StockPage: TWizardPage;
  chkNoStock: TNewCheckBox;

  LogPage: TWizardPage;
  cbLogLevel: TNewComboBox;

procedure InitializeWizard;
begin
  PaisPage := CreateCustomPage(
    wpSelectDir,
    'PaÃ­s por defecto',
    'Elija el paÃ­s con el que el sistema operarÃ¡ (afecta moneda y reglas de cantidades).'
  );

  cbPais := TNewComboBox.Create(PaisPage.Surface);
  cbPais.Parent := PaisPage.Surface;
  cbPais.Left := ScaleX(0);
  cbPais.Top := ScaleY(8);
  cbPais.Width := PaisPage.SurfaceWidth;
  cbPais.Style := csDropDownList;
  cbPais.Items.Add('Paraguay');
  cbPais.Items.Add('PerÃº');
  cbPais.Items.Add('Venezuela');
  cbPais.ItemIndex := 0;

  ListadoPage := CreateCustomPage(
    PaisPage.ID,
    'Tipo de listado',
    'Elija quÃ© tipo de Ã­tems mostrarÃ¡ el listado y el autocompletar dentro del sistema.'
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

  LogPage := CreateCustomPage(
    StockPage.ID,
    'Logging centralizado',
    'Seleccione el nivel de logging. Los archivos se guardarÃ¡n en Documentos\Cotizaciones\logs.'
  );

  cbLogLevel := TNewComboBox.Create(LogPage.Surface);
  cbLogLevel.Parent := LogPage.Surface;
  cbLogLevel.Left := ScaleX(0);
  cbLogLevel.Top := ScaleY(8);
  cbLogLevel.Width := LogPage.SurfaceWidth;
  cbLogLevel.Style := csDropDownList;
  cbLogLevel.Items.Add('ERROR');
  cbLogLevel.Items.Add('WARNING');
  cbLogLevel.Items.Add('INFO');
  cbLogLevel.Items.Add('DEBUG');
  cbLogLevel.ItemIndex := 2;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  PaisSel, ListadoSelUpper, AllowStr, LogLevelSel: string;
  FJson: string;
  ConfJson: string;
  Cmd, Params: string;
  ResultCode: Integer;
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

    case cbLogLevel.ItemIndex of
      0: LogLevelSel := 'ERROR';
      1: LogLevelSel := 'WARNING';
      3: LogLevelSel := 'DEBUG';
    else
      LogLevelSel := 'INFO';
    end;

    FJson := ExpandConstant('{app}\config\app_config.json');
    ConfJson :=
      '{' + #13#10 +
      '  "country": "' + PaisSel + '",' + #13#10 +
      '  "listing_type": "' + ListadoSelUpper + '",' + #13#10 +
      '  "allow_no_stock": ' + AllowStr + ',' + #13#10 +
      '  "log_dir": "' + ExpandConstant('{userdocs}\Cotizaciones\logs') + '",' + #13#10 +
      '  "log_level": "' + LogLevelSel + '",' + #13#10 +
      '  "update_mode": "ASK",' + #13#10 +
      '  "update_check_on_startup": true,' + #13#10 +
      '  "update_manifest_url": "' + '{#UpdateManifestUrl}' + '",' + #13#10 +
      '  "update_flags": "/CLOSEAPPLICATIONS"' + #13#10 +
      '}';

    SaveStringToFile(FJson, ConfJson, False);

    { Ocultar y proteger el JSON }
    SetFileAttributes(FJson, MY_ATTR_HIDDEN or MY_ATTR_SYSTEM);
    Cmd := ExpandConstant('{cmd}');
    Params := '/c icacls "' + FJson + '" /inheritance:r /grant:r "SYSTEM":F "Administrators":F "Users":R';
    Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;



