# Cotizador Piloto 2.0.38-piloto.1

Edición independiente para evaluar los cambios actuales de histórico compartido
y contexto por país/empresa. No incorpora las reglas experimentales de precios
del cliente `piloto-precios`: EFAPI `dev` admite ese cliente por separado.

## Distribución y datos

- Release solicitada como **draft + prerelease**, nunca Latest.
- Instalador `Setup_CotizadorPiloto_2.0.38-piloto.1.exe`, sin manifiesto `cotizador.json`.
- Carpeta fija `%LOCALAPPDATA%/Programs/CotizadorPilotoHistorico`; se rechaza `/DIR`
  que intente instalar en otra carpeta. AppId y mutex propios.
- Datos: `Documentos/Cotizaciones Piloto Historico/data/app.sqlite3`.
- No se abre, copia, migra ni borra la DB de producción. Tampoco se recuperan sus
  settings como fallback. El piloto inicia con datos propios y PID nuevo.
- No contiene DB, WAL, SHM ni helper de actualización. El updater retorna antes
  de leer manifiestos, incluso con settings remotos que pidan actualizar.
- Las actualizaciones del piloto son manuales mediante otro instalador de este
  perfil; las bases y documentos están fuera de sus archivos instalados.
- Las conexiones de negocio frozen conservan EFAPI de producción según el
  contrato actual. **Aislar la DB local no convierte el servidor en un sandbox**:
  usar una identidad piloto autorizada y documentos de prueba para modificaciones
  y eliminaciones. No se ejecutaron operaciones sobre ese servidor al construir.

## Preparación del piloto compartido

EFAPI debe desplegar el código compatible de `dev`, aplicar la migración nueva
en el destino identificado/autorizado y habilitar al usuario de prueba. Esa
operación sigue pendiente y no forma parte de la construcción del instalador.
Antes de publicar SQL, volver a revisar todas las ramas remotas y su numeración.
Las instalaciones antiguas pueden seguir trabajando: no se exige una versión
mínima. Los conflictos documentales no se sobrescriben automáticamente.

## Fuentes

Worktree propio desde `main` b30ba7d. Se incorporó el estado de los 27 archivos
del trabajo paralelo de contexto, junto con la implementación compartida, sin
modificar el checkout original ni la rama `piloto-precios`. El único conflicto
de integración estuvo en Abrir Cotización: conserva autorización/origen y usa
el ID de la instalación actual para la nueva copia. La moneda del snapshot
sincronizado permanece histórica; el catálogo nuevo sigue las reglas por país.

## Validación

99 pruebas dirigidas de SQLite, sincronización, Qt/contextos, PDF y aislamiento,
más 22 pruebas de EFAPI en PostgreSQL aislado. Estas últimas incluyen el escenario
de dos SQLite independientes sobre HTTP usando las fuentes integradas del piloto.
El test local de precios encontrado correspondía a funciones exclusivas del
piloto de precios ausentes en esta base; no se trasladó como prueba de este piloto.
La comprobación `--pilot-self-check` prueba el binario con SQLite sintético,
red bloqueada y directorios temporales. No ejecuta el arranque normal ni Inno.
El instalador se compila y se inspecciona su contrato; la instalación interactiva,
el piloto entre equipos, impresoras y latencia real quedan para la prueba del usuario.

```powershell
./tools/build_pilot.ps1 -Python C:/ProyectosEF/Cotizador/.venv/Scripts/python.exe
```

Este script no cambia versiones, dependencias, ramas, commits ni releases. Para
esta entrega se conserva un commit local de las fuentes y se suben solo los
artefactos solicitados al borrador de `zaphatito/CotizadorReleases`.
