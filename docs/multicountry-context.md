# Contexto multipaís de cotización

Implementado sobre `main`, sin migración SQLite (continúa versión 45), instalador ni publicación. Los cambios puntuales de EFAPI están en su checkout de trabajo. EF System queda fuera del alcance.

## Contrato

El perfil de país es la autoridad de moneda base para nuevas cotizaciones, caché, históricos y reintentos: BO/BOL/BOLIVIA → BOB; PE → PEN; PY → PYG; VE → USD. Se conservan las monedas secundarias existentes. Los países desconocidos se rechazan en el contexto y en el envío a EFAPI.

`CatalogScope` conserva los identificadores autorizados y sus aliases; el identificador remoto de grupo no se reconstruye. `QuoteContext` es inmutable. El editor captura antes de construir los controles su país, empresa, propietario, monedas y preferencias de listado.

El modal de creación aparece incluso con un solo país. La empresa solo se muestra si hay varias autorizadas. Seleccionar o cancelar no cambia el catálogo global. La confirmación vuelve a comprobar la asignación y el catálogo. Menú, histórico y preparación del asistente utilizan el selector; un plan pendiente del asistente conserva el contexto que se seleccionó para resolver sus productos.

Cada editor tiene artículos, catálogo, recomendaciones y stock propios. Los eventos se filtran por scope y propietario. Una asignación retirada bloquea la emisión y la incorporación de productos, manteniendo los artículos existentes. La matriz abierta desde el editor solo muestra sus tiendas; la matriz general mantiene todas las asignaciones. Cero confirmado, SKU sin información y tienda sin snapshot se presentan de forma diferenciada.

Las tasas se siguen almacenando por pareja de monedas. `rates_updated` recarga las tasas disponibles sin sustituir la aplicada en otras ventanas. Los diálogos rechazan tasas no positivas o no finitas.

## Históricos y documentos

`quote_context_service.quote_context_from_header` centraliza la recuperación para duplicación, PDF, tickets, etiquetas y reintentos. El país procede de la cabecera o del prefijo histórico cuando falta; una contradicción se comunica. En duplicación remota se resuelven aliases de empresa y se comprueban asignación y propietario. Para legacy se admite la única empresa autorizada del país cuando el dato antiguo no permite una correspondencia fiable.

Una base histórica incompatible se sustituye solo en el contexto de lectura. No se convierten ni escriben importes al abrir. Se mantienen cantidades, factores, descuentos, precios y snapshots mostrados, incluso para SKU retirados o sin catálogo. El stock se toma del contexto vigente. Una edición explícita invalida el snapshot correspondiente y permite recalcular.

La moneda mostrada y la tasa histórica permanecen aplicadas; si coincide con la base, la tasa efectiva es 1. Si falta una tasa secundaria, se muestran los snapshots disponibles y se solicita una tasa antes de recalcular. Guardar una duplicación crea otro registro/código/archivo y deriva nuevamente la base por país. Las nuevas cotizaciones remotas exigen contexto explícito completo. Reserva provisional, confirmación, regeneración y reenvío usan el contexto persistido.

## EFAPI

`POST /service/db/getCotizadorCatalogStock` conserva todos los grupos asignados y añade `base_currency` por grupo. El campo participa en `manifest_revision`; `group_key`, revisiones de catálogo, contratos de precios y snapshots por tienda permanecen compatibles. Swagger documenta las cuatro monedas. El cliente acepta también el contrato anterior sin `base_currency`, pues siempre deriva la base del país.

No hay SQL ni cambios de esquema PostgreSQL. No se aplicaron migraciones ni se conectó a una base compartida.

## Validación ejecutada

- 232 pruebas pytest aprobadas con SQLite temporal, bloqueo de apertura de la DB instalada y red simulada. Incluyen las 15 combinaciones no vacías y cuatro ventanas Qt simultáneas con el mismo SKU, catálogos y stocks distintos.
- Regresiones de base incorrecta o ausente, snapshots, tasa pendiente, empresas ambiguas, país contradictorio, revocación, cero/desconocido, persistencia de duplicados y rechazo de contexto incompleto sin registros parciales.
- Suites existentes de caché y sincronización, correlativos provisionales, contexto de documentos, reglas de stock, catálogo y asistente aprobadas.
- 8 pruebas Jest aprobadas en EFAPI (contrato multipaís con DB simulada y conciliación).
- Sintaxis de los archivos Python modificados y JavaScript afectado, `git diff --check` y Prettier de controlador/configuración/prueba EFAPI aprobados.
- Revisión visual de capturas Qt del selector y editores BO, PE, PY y VE, con importes históricos conservados.

Comandos:

```powershell
# Cotizador; conftest impide abrir SQLite fuera del sandbox temporal.
.venv/Scripts/python.exe -m pytest tests --ignore=tests/test_money.py --ignore=tests/test_pricing.py -q --tb=short

# EFAPI; ambas suites simulan database.js, sin importar app.js.
npx.cmd jest tests/catalogStockMulticountry.test.js tests/conciliacion.test.js --runInBand --watch=false
```

Limitaciones: la colección completa encuentra dos pruebas legacy incompatibles: `test_money.py` importa el módulo inexistente `src.money`; `test_pricing.py` importa `round_line_total`, que no existe en la implementación actual. `dbRoutes.js` ya falla Prettier en HEAD; se conservó el cambio acotado de Swagger sin reformatear el archivo completo. La integración HTTP/PostgreSQL queda pendiente de un entorno de desarrollo identificado; las capturas Qt no sustituyen una prueba de impresoras físicas.

Las pruebas locales permanecen bajo las reglas de exclusión existentes de Git; no se cambió esa política ni se crearon commits. Se preservaron los cambios previos ajenos en productos, precios, búsqueda y envío de presupuestos. Instalador y despliegue de EFAPI son entregas separadas.
