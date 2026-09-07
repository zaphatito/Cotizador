# Histórico compartido: preparación de integración

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
- Histórico: país/empresa y moneda visibles, códigos completos, estado de
  sincronización, informe de parciales/incidencias y resolución explícita de
  conflictos. Actualizar el listado conserva página y selección.
- «Abrir Cotización» sigue creando una copia con la instalación actual. Los
  documentos originales conservan contexto, importes e items de su snapshot.
- PDF: un documento descargado puede regenerarse desde el snapshot sin una ruta
  previa. Cambios de metadata invalidan la ruta anterior. No se transfieren CMD.

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
