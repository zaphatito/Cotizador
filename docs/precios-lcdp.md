# Precios comerciales de LCDP

Rama del Cotizador: `precios`, creada desde `main` (`c455f48`).
Referencia: `lcdp/precios`, commit `7222a88`. No se incorporaron commits
de `piloto-precios`.

## Correspondencia

| LCDP (`public/js/ventas/punto-venta.js`) | Cotizador |
| --- | --- |
| `precioTotalCProducto`, `precioTotalSeleccionado` | Precio comercial redondeado a dos decimales; máximo, mínimo y oferta |
| `redondearMonto`, cálculo del carrito | `src/lcdp_pricing.py`: precio → bruto de línea → total final → descuento por diferencia |
| `calcularDescuentoModal`, métodos 1/2 | Porcentaje o importe convertido al porcentaje entero más cercano; total final editable |
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
y descuento 13.00 en todos los países.

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

## Validación

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_lcdp_pricing.py tests/test_window_currency_context.py tests/test_peru_base_units.py tests/test_multicountry_regressions.py tests/test_pdf_context_isolation.py tests/test_shared_history_ui.py -q
```

Las pruebas dirigidas pasan con SQLite temporal, red bloqueada y Qt offscreen.
Queda pendiente la prueba manual con un catálogo operativo, impresión física
y sincronización contra un servidor de desarrollo autorizado. No se ejecutó
`main.py`, ni se publicó una versión, ni se crearon commits.


## Instalador sin actualización automática

Sistema completo (no es el lanzador offline de pruebas). `automatic_updates_allowed()`
retorna `False` incluso si SQLite o la configuración remota habilitan actualizaciones.
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


## Revisión 2: importe y tema oscuro

Al introducir un importe o total final, se calcula el porcentaje entero más
cercano (empates hacia arriba) y se recalculan descuento y total con ese
porcentaje. Ejemplo: 12.50 sobre 100 → 13% → descuento 13.00.
La regla aplica a la entrada del diálogo y a la edición directa en todos los
países. Se mantienen los snapshots históricos y el mínimo de 1.00.
Los campos del diálogo no muestran botones nativos sin contraste; el ajuste
se confirma al terminar de escribir. Instalador actualizado con sufijo `_r2`.
