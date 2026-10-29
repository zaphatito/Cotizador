# Histórico compartido

## Cotizaciones del catálogo

El Sync puede entregar documentos de otro autor únicamente con el permiso
`access: {relation: 'recipient', recipient_owner_id, can_edit: false}` para el
usuario conectado. SQLite conserva ese destinatario y el propietario original;
el cursor continúa perteneciendo al receptor. El histórico filtra por permisos
y ámbito. «Abrir Cotización» crea una copia con la identidad del usuario actual;
el destinatario no puede cambiar estado, pago, origen ni eliminar el original.

`origin: 'catalogue'` identifica la procedencia del documento. El protocolo
sincroniza únicamente datos comerciales, permisos y recibos. No incluye PDF,
estado de un PDF remoto, hashes de archivos, rutas, bytes ni Base64; tampoco
existen operaciones para subir o descargar esos archivos desde EFAPI.

Cada dispositivo genera y conserva su PDF con la plantilla y el generador local,
usando exclusivamente el snapshot confirmado: autor, código, fecha, cliente,
moneda, importes y observaciones generales y por línea. No consulta precios,
productos ni fotografías vivos para reconstruirlo. Si ya hay un archivo local
vigente, «Abrir PDF» lo reutiliza. Si falta, la generación ocurre en un worker;
un reemplazo atómico evita publicar archivos incompletos. Los errores muestran
«PDF local pendiente» y permiten reintentar con «Abrir PDF», sin emitir otra
cotización, reservar otro correlativo ni modificar el documento sincronizado.
El generador original del Cotizador es la referencia del PDF, también para Perú.
Las adaptaciones para lograr paridad se realizan en el catálogo nuevo; la
integración no cambia el diseño, la paginación ni los cálculos del Cotizador.

La confirmación `postCatalogueQuoteReceipt` se envía después de persistir
documento y cursor. Una cola SQLite conserva el recibo si falla la red y lo
reintenta de forma idempotente. El esquema local 47 añade las tablas de
destinatarios y recibos; no cambia los snapshots existentes.

Pruebas dirigidas, con SQLite temporal y red simulada:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_catalogue_shared_quotes.py tests/test_shared_quote_sync.py tests/test_shared_history_ui.py tests/test_quote_context_service.py -q
```

La validación de estos contratos no implica una migración de la base operacional,
una publicación del servidor ni una distribución del instalador.

## Antecedentes de integración

Worktree independiente desde `main` (`b30ba7d`). La base SQLite pasa de 45 a 46.
La contraparte de EFAPI incluye `database/1.5.17.sql` y
`docs/cotizador-sync.md`, con contrato, auditoría, orden de despliegue y pausa.

## Cambios

- `src/server_identity.py`: identidades con campos nombrados, rechazo de cuentas
  técnicas/variantes, parejas invertidas y longitudes inválidas.
- `sqlModels/quote_sync_repo.py`: UUID, snapshots, outbox inmutable, generación
  local, ACK, cursores, eventos diferidos y propuestas en conflicto.
- `src/quote_sync_service.py`: coordinador sin dependencia de Qt, envío/descarga
  por ámbito y reintentos persistidos por documento.
- `src/quote_sync_adapter.py`: reconciliación inicial, proyección compatible con
  EFAPI e importación de snapshots sin archivos externos ni actualización de la
  ficha viva del cliente por una cotización histórica.
- Histórico: moneda visible, sin columnas de país/empresa o sincronización,
  sin informe ni etiqueta de sincronización. Los conflictos conservan su acción
  de resolución en el menú contextual. Actualizar el listado conserva página y
  selección; la sincronización sigue funcionando en segundo plano.
- «Abrir Cotización» sigue creando una copia con la instalación actual. Los
  documentos originales conservan contexto, importes e items de su snapshot.
- PDF local de cualquier origen: un documento recibido puede generarse desde el snapshot sin una ruta
  previa. Estado, pago y origen web conservan la referencia local porque no se
  imprimen en el PDF. Los cambios de contenido siguen invalidándola. El listado
  también encuentra archivos locales por país, instalación, número y cliente;
  una referencia invalidada se regenera al abrir para asegurar contenido actual.
  No se transfieren CMD.

La activación la decide EFAPI por usuario y está desactivada por defecto. Después
de habilitarse, una pausa no devuelve la instalación al envío legacy sin revisión.

## Pruebas

Usar un intérprete del entorno virtual que tenga las dependencias del proyecto:

```powershell
RUTA_VENV/Scripts/python.exe -m pytest tests/test_shared_quote_sync.py tests/test_shared_history_ui.py -q
```

Las pruebas usan SQLite temporal y un modelo Qt aislado. No importan la
configuración instalada ni arrancan `main.py`. La prueba por HTTP se inicia desde
EFAPI con `COTIZADOR_TEST_CLIENT_ROOT` apuntando a este worktree y
`COTIZADOR_TEST_PYTHON` apuntando a ese intérprete. Crea dos SQLite temporales y
usa un PostgreSQL aislado; cubre cinco → seis, múltiples ámbitos, timeout tras
commit, propuestas simultáneas y eliminación compartida.

La medición por HTTP corresponde a confirmación → persistencia local bajo red
de pruebas. El piloto debe medir confirmación → visualización con las dos GUIs,
red real, entrada/salida de primer plano y reinicio de ambas instalaciones.

## Trabajo paralelo pendiente de integrar

El checkout original tiene cambios sin commit en contexto, precios, histórico,
API y repositorios. No se han incorporado como si fueran una base terminada.
Es necesario identificar el commit final y fusionarlo en este worktree.

Puntos de integración que requieren revisión conjunta:

| Archivo | Criterio que debe conservarse |
| --- | --- |
| `sqlModels/quotes_repo.py` | El snapshot sincronizado prevalece sobre defaults y sobre cambios posteriores de la ficha del cliente. |
| `src/quote_context_service.py` | Autorizar por propietario lógico y ámbito; conservar el origen del documento. |
| `src/widgets_parts/quote_history_dialog.py` | La copia usa la instalación actual, el original conserva su contexto; regenerar PDF sin ruta previa. |
| `src/api/presupuesto_client.py` | Reutilizar las reglas de precios/gramaje definitivas al construir la proyección; mantener sesión de instalación para los ámbitos remotos. |

En EFAPI también coincide `catalogStockController.js`: el cambio de sincronización
solo consolida asignaciones del usuario habilitado, sin reemplazar las reglas de
precios. Antes de commit/push revisar nuevamente versiones remotas y reasignar
el SQL si `1.5.17` dejó de ser la siguiente versión libre.

## Pendiente para cerrar la entrega

Se integró una copia del estado actual del cambio paralelo para la edición piloto
(ver `piloto-release.md`); la integración definitiva en main sigue pendiente.
Faltan la prueba de materialización comercial completa
sobre copia del esquema real; auditoría de identidades/códigos reales; actualización
de las instalaciones participantes; piloto de GUI y medición de latencia. No se
ha aplicado SQL a la base central compartida. El instalador piloto se entrega
como borrador, sin distribución pública ni actualización automática.
