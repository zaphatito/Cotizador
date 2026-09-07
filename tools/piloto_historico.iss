; Distribución independiente. No reutiliza AppId, carpetas ni desinstalador de producción.
#ifndef ProjectRoot
  #error ProjectRoot es obligatorio
#endif
#define PilotVersion "2.0.38-piloto.1"
#define PilotFolder "CotizadorPilotoHistorico"

[Setup]
AppId={{D0B3C583-E88B-45FA-9E4F-BA06C00F3E8A}}
AppName=Cotizador Piloto
AppVersion={#PilotVersion}
AppPublisher=EF Perfumes
VersionInfoVersion=2.0.38.1
DefaultDirName={localappdata}\Programs\{#PilotFolder}
DefaultGroupName=Cotizador Piloto
UsePreviousAppDir=no
PrivilegesRequired=lowest
DisableDirPage=yes
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ProjectRoot}\Output\piloto-historico
OutputBaseFilename=Setup_CotizadorPiloto_{#PilotVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=CotizadorPiloto.exe
RestartApplications=no
UninstallDisplayIcon={app}\CotizadorPiloto.exe
InfoBeforeFile={#ProjectRoot}\docs\piloto-instalacion.txt

[Languages]
Name: spanish; MessagesFile: compiler:Languages\Spanish.isl

[Files]
Source: "{#ProjectRoot}\dist\piloto-historico\CotizadorPiloto\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.sqlite3,*.sqlite3-wal,*.sqlite3-shm,*.db,*.db-wal,*.db-shm,config\config.json,config\cotizador.json,updater\*"
Source: "{#ProjectRoot}\output\pdf\Cotizador_Piloto_Resumen_y_Guia.pdf"; DestDir: "{app}"; DestName: "Guia_del_Piloto.pdf"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Cotizador Piloto"; Filename: "{app}\CotizadorPiloto.exe"; WorkingDir: "{app}"; AppUserModelID: "Cotizador.PilotoHistorico.1"
Name: "{userdesktop}\Cotizador Piloto"; Filename: "{app}\CotizadorPiloto.exe"; WorkingDir: "{app}"; AppUserModelID: "Cotizador.PilotoHistorico.1"
Name: "{userprograms}\Guía del Cotizador Piloto"; Filename: "{app}\Guia_del_Piloto.pdf"

; Sin InstallDelete, UninstallDelete, copia de datos ni ejecución automática de la app.
[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var Expected, Chosen: String;
begin
  Expected := ExpandFileName(ExpandConstant('{localappdata}\Programs\{#PilotFolder}'));
  Chosen := ExpandFileName(ExpandConstant('{app}'));
  if CompareText(RemoveBackslashUnlessRoot(Chosen), RemoveBackslashUnlessRoot(Expected)) <> 0 then
    Result := 'El piloto solo puede instalarse en su carpeta independiente. No se permite usar la carpeta del sistema habitual.'
  else
    Result := '';
end;
