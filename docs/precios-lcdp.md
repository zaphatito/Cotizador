# Precios comerciales de LCDP

Rama del Cotizador: `precios`, creada desde `main` (`c455f48`).
Referencia: `lcdp/precios`, commit `7222a88`. No se incorporaron commits
de `piloto-precios`.

## Correspondencia

| LCDP (`public/js/ventas/punto-venta.js`) | Cotizador |
| --- | --- |
| `precioTotalCProducto`, `precioTotalSeleccionado` | Precio comercial redondeado a dos decimales; máximo, mínimo y oferta |
| `redondearMonto`, cálculo del carrito | `src/lcdp_pricing.py`: precio → bruto de línea → total final → descuento por diferencia |
| `calcularDescuentoModal`, métodos 1/2 | Porcentaje o importe; PY/BO conservan el importe exacto y PE/VE convierten la entrada monetaria al porcentaje entero más cercano; total final editable |
| `descuentoIncumpleMinimo` | Descuentos hasta 99% y total de línea mínimo 1.00 en la moneda base de cada país |
| Revalidación al cambiar cantidades | Retira el descuento si deja de ser válido y muestra el motivo |

El catálogo Excel enviado por `SendEmailCotizador` y
`SendEmailCotizadorFranquicia` ya incorpora IVA (a cuatro decimales).
El Cotizador redondea ese precio comercial, sin sumar IVA nuevamente.
Las presentaciones combinan los componentes antes del redondeo final.
Las cantidades mantienen los factores y excepciones por país existentes.

Ejemplo: 2 × 5.99 con 25% de descuento produce bruto 11.98,
total 8.99 y descuento 2.99. No se redondea primero el descuento a 3.00.
Un descuento introducido como importe de 12.50 sobre 100 se ajusta a 13%
y descuento 13.00 en PE/VE. En PY/BO conserva el descuento de 12.50.

El editor, el selector de precios y la recalculación utilizan la misma regla.
PDF, impresión e histórico consumen los importes de línea guardados. El API
envía esos snapshots sin reconstruir el descuento desde un porcentaje.
Reabrir un histórico no modifica sus importes originales.

El mínimo se aplica a PE/PEN, PY/PYG, VE/USD y BO/BOB, también al descuento
automático de efectivo en Paraguay. Las líneas sin descuento pueden conservar
precios inferiores a 1.00.

Los ajustes existentes de efectivo en Paraguay se conservan y utilizan el
redondeo de total antes de calcular el descuento. Esta adaptación no agrega
infraestructura de cobro, cupones, recargos POS ni impuestos fiscales al
Cotizador.

## Auditoría de Paraguay y Bolivia — 29 de octubre de 2026

Se descargaron los catálogos de producción mediante `getCotizadorCatalogStock`,
el mismo endpoint que consume el Cotizador. Se reutilizó la identidad instalada
para PE y se registraron únicamente las dos instalaciones de prueba autorizadas
para `LCDP3PY/003` y `LCDP1BOL/001`. No se cambió la configuración instalada,
no se enviaron cotizaciones y no se ejecutó SQL contra PostgreSQL. Los dos
registros de prueba permanecen registrados; esta revisión no los elimina.

Comparación con los cálculos de `main` (`0f0d41e`) sobre los catálogos LCDP
recibidos, en los tres niveles de precio y varias cantidades:

| País | Productos | Presentaciones | Líneas evaluadas |
| --- | ---: | ---: | ---: |
| PE | 1.106 | 1 | 11.190 |
| PY | 1.265 | 26 | 11.619 |
| BO | 302 | 26 | 2.952 |

Las 25.761 líneas conservaron los factores de país y coincidieron con los
importes del payload API. Se verificó también que volver a confirmar la cantidad
no cambia el precio seleccionado. Para productos unitarios y presentaciones se
evaluaron cantidades 1, 12 y 1.000; para peso PE, 0,001/0,05/1/25 kg, y para
peso PY/BO, cantidades internas 1/50/1.000. Las presentaciones se evaluaron con
sus precios de catálogo; la suma de componentes antes del redondeo tiene además
una prueba independiente.

- PY: las esencias evaluadas no cambian de importe. El mayor cambio unitario
  es 2,1277% (`VOL001`, 0,1175 → 0,12). En las líneas evaluadas, el máximo
  relativo es 2,8571% (`ALH001`, 12 unidades, 0,35 → 0,36). El máximo absoluto
  es 5 PYG a 1.000 unidades. Ningún precio positivo pasa a cero.
- BO: el mayor cambio relativo de línea es 0,0849% (`PCP10003`, 12 unidades,
  47,12 → 47,16 BOB). Para las esencias, el máximo observado es 0,0095%
  (50 unidades, 104,99 → 105,00 BOB). El máximo absoluto es 4,90 BOB a 1.000
  unidades. Ningún precio positivo pasa a cero.
- PE: seis productos de empaque llegan con precio 0,0001 y pasan a 0,00,
  conforme a la regla de dos decimales confirmada. Por ejemplo, 1.000 unidades
  pasan de 0,10 a 0,00 PEN. No se añade un mínimo artificial al precio.

Correcciones en fuente y regresiones:

- PY/BO: el descuento por importe y el total final editable conservan el importe
  exacto, incluso tras cambiar la cantidad. PE mantiene su comportamiento.
- PY: efectivo respeta el tope de 99% y conserva el porcentaje del usuario al
  activarlo/desactivarlo en la misma edición. Ya no retira todo el descuento al
  sobrepasar el tope ni acepta porcentajes no finitos.
- Todos los países: un precio mínimo positivo que redondea a cero no se
  sustituye por el máximo al editar la cantidad.
- VE: el payload de productos por peso aplica los mismos gramos que el editor,
  evitando una discrepancia por factor 50. Este caso se comprobó con datos
  sintéticos; no se descargó un catálogo de producción venezolano.

Los catálogos y el diagnóstico detallado permanecen únicamente en `tmp/`,
ignorado por Git. Estos resultados corresponden a los datos recibidos en esta
fecha y a las cantidades indicadas; no certifican futuros catálogos ni una
sincronización real de cotizaciones. No se compiló ni publicó un instalador.

Validación final: 242 pruebas dirigidas aprobadas (precios, importes, gramajes,
monedas, ventanas por país, contexto PDF e histórico de UI); otras 960
combinaciones sintéticas respetaron la cota de error del redondeo por unidad.
La sintaxis de los cinco archivos Python modificados es válida y
`git diff --check` no detecta errores de espacios.

La validación ampliada detectó también dos fallos ajenos a precios en
`test_shared_quote_sync.py`, al abrir un PDF inexistente: sus dobles
`SimpleNamespace` no implementan `_request_local_pdf`, requerido por los cambios
locales previos de histórico. No se modificó ese flujo durante esta auditoría.

## Validación

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_lcdp_pricing.py tests/test_window_currency_context.py tests/test_peru_base_units.py tests/test_multicountry_regressions.py tests/test_pdf_context_isolation.py tests/test_shared_history_ui.py -q
```

Las pruebas dirigidas pasan con SQLite temporal, red bloqueada y Qt offscreen.
Queda pendiente la prueba manual con un catálogo operativo, impresión física
y sincronización contra un servidor de desarrollo autorizado. No se ejecutó
`main.py`, ni se publicó una versión, ni se crearon commits.


## Instaladores de prueba hasta 2.0.50, sin actualización automática

Los instaladores de prueba anteriores eran el sistema completo (no el lanzador offline).
En aquellas compilaciones, `automatic_updates_allowed()` retornaba `False` incluso si
SQLite o la configuración remota habilitaban actualizaciones.
El instalador también siembra `update_mode=OFF` y `update_check_on_startup=false`.
La conexión y sincronización normales del sistema se mantienen.

Compilación del instalador completo de pruebas, separada del release público:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist/precios-2.0.50 --workpath build/precios-2.0.50 Utilidades/sistema_cotizaciones.spec
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' '/DBuildDir=C:\ProyectosEF\Cotizador\dist\precios-2.0.50\SistemaCotizaciones' 'Output/script inno.iss'
```

Salida: `Output/precios/Setup_SistemaCotizaciones_2.0.50_precios_sin_actualizacion.exe`.
Mantiene la identidad del instalador normal: instalarlo actualiza el sistema completo,
no crea una segunda instalación aislada. No incluye bases de datos de este equipo.

### Instalador con la integración del catálogo

La entrega local del 19 de octubre conserva el mismo instalador, páginas,
identidad, destino y bloqueo de actualizaciones. Incluye el código actual de
sincronización del catálogo y el generador PDF original, sin modificarlo.
Solo cambia el nombre del archivo de entrega y su carpeta de salida:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist/catalogo-2.0.50-20261019 --workpath build/catalogo-2.0.50-20261019 Utilidades/sistema_cotizaciones.spec
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' '/DBuildDir=C:\ProyectosEF\Cotizador\dist\catalogo-2.0.50-20261019\SistemaCotizaciones' '/OC:\ProyectosEF\Cotizador\Output\catalogo' '/FSetup_SistemaCotizaciones_2.0.50_catalogo_sin_actualizacion' 'Output/script inno.iss'
```

El binario se comprueba con `--precios-self-check <json-temporal>` sin iniciar
la aplicación, tocar SQLite ni contactar servicios. Se verificaron también los
módulos compilados de sincronización y PDF contra sus fuentes. El paquete no
contiene bases de datos, WAL/SHM ni `.env`. El instalador se compiló, sin instalarlo
en el equipo de desarrollo ni ejecutar el pipeline de release o crear commits.
Como Precios, utiliza el servidor habitual del ejecutable instalado; este build
no publica EFAPI ni aplica migraciones a bases compartidas.


## Revisión 2: importe y tema oscuro

La auditoría del 29 de octubre sustituye esta regla para PY/BO, que conservan
el importe exacto. El texto siguiente describe el comportamiento de revisión 2.

Al introducir un importe o total final, se calcula el porcentaje entero más
cercano (empates hacia arriba) y se recalculan descuento y total con ese
porcentaje. Ejemplo: 12.50 sobre 100 → 13% → descuento 13.00.
La regla aplica a la entrada del diálogo y a la edición directa en todos los
países. Se mantienen los snapshots históricos y el mínimo de 1.00.
Los campos del diálogo no muestran botones nativos sin contraste; el ajuste
se confirma al terminar de escribir. Instalador actualizado con sufijo `_r2`.


## Integración en el release público 2.0.51

Las correcciones se integran en `dev` y `main`. Se restaura el actualizador normal
de producción (`automatic_updates_allowed() == not IS_PILOT`) y el instalador
`Setup_SistemaCotizaciones_2.0.51.exe`. El perfil piloto independiente sigue manual.
La comprobación del binario público usa `--release-self-check <json-temporal>` sin
iniciar la aplicación ni abrir SQLite. Los dos fallos de pruebas PDF anotados en la
auditoría se corrigieron adaptando la simulación al trabajo asíncrono y comprobando
que el documento se abre únicamente después de generarse.
