# Contrato servidor: configuración inicial y sincronización multiámbito

## Objetivo

El cotizador deja de pertenecer a una única tienda, empresa o país. La identidad
del equipo es `(username, id_cotizador, pid)` y puede tener varias asignaciones:

`cotizador -> país/empresa -> una o más tiendas -> stock por tienda`

El país y la empresa guardados en `settings` son solamente el ámbito
predeterminado para abrir la interfaz. No son una autorización ni deben limitar
la respuesta del servidor.

### Selección de modo offline

`username` e `id_cotizador` forman un par opcional. Si ambos quedan vacíos en el
instalador, el cliente trabaja en modo offline: no crea una solicitud pendiente,
no invoca este endpoint y no ejecuta sincronización, verificación de acceso ni
envíos de cotizaciones o etiquetas. La configuración, catálogo Excel, clientes,
histórico, correlativos provisionales y salidas físicas permanecen locales.

Completar solo uno de los dos valores es inválido. Para activar el modo servidor
deben configurarse ambos.

## 1. Registro inicial idempotente

### Compatibilidad con el API actual

El cotizador no depende de un endpoint nuevo para registrar una instalación.
Durante la instalación valida y conserva localmente la configuración completa,
pero registra la identidad en el endpoint existente:

`POST /service/db/verifyCotizador`

La solicitud enviada al API se limita a `pid`, `id_cotizador`, `user`, país,
empresa, telemarketing y metadatos de firma compatibles con el contrato
legacy. Esto permite volver a ingresar usuarios sin modificar `efapi`.

El contrato de bootstrap completo descrito debajo queda como referencia para
una futura implementación del servidor; la versión actual del cotizador no
invoca esa ruta.

Contrato futuro del endpoint nuevo:

`POST /service/db/bootstrapCotizadorConfiguration`

Usa el mismo `Bearer` del login actual. El cuerpo tiene `schema_version=1`, un
`idempotency_key` UUID, `pid`, identidad, preferencias, ámbito predeterminado y
una colección `assignments` agrupada por país/empresa. Cada tienda puede incluir
un snapshot completo de stock y cada grupo puede incluir el catálogo inicial.

Ejemplo reducido:

```json
{
  "schema_version": 1,
  "idempotency_key": "8c2b5604-bca7-43a5-bd31-fd0740c03fb2",
  "pid": "123e4567-e89b-12d3-a456-426614174000",
  "identity": {
    "username": "operador",
    "id_cotizador": "COT01",
    "telemarketing": false
  },
  "preferences": {
    "listing_type": "AMBOS",
    "allow_no_stock": false,
    "enable_ai": false,
    "enable_recommendations": true
  },
  "default_scope": {
    "country_code": "PY",
    "company_type": "EF PERFUMES",
    "store_code": "ASU01"
  },
  "assignments": [
    {
      "country": {"code": "PY", "name": "PARAGUAY"},
      "company": {"name": "EF PERFUMES"},
      "catalog": {
        "products": [],
        "presentations": [],
        "presentation_products": [],
        "departments": [],
        "genders": []
      },
      "stores": [
        {
          "code": "ASU01",
          "name": "Asunción Centro",
          "stock": {
            "available": true,
            "items": [{"codigo": "SKU-1", "cantidad": 12}]
          }
        }
      ]
    }
  ]
}
```

Reglas del endpoint:

1. Persistir todo en una única transacción.
2. Registrar `idempotency_key` con hash del cuerpo. La repetición del mismo UUID
   y cuerpo devuelve el recibo previo sin duplicar tiendas ni stock. El mismo
   UUID con otro cuerpo devuelve conflicto.
3. Resolver/upsert país, empresa y tienda por claves estables; nunca interpretar
   `id_cotizador` como `id_tienda`.
4. `assignments` es la colección completa autorizada para el cotizador. Una
   tienda no incluida deja de estar asignada, pero no debe borrarse globalmente.
5. Un bloque `catalog` o `stock` ausente significa “conservar el snapshot del
   servidor”. Un bloque presente es snapshot completo, incluido el cero real.
6. Validar códigos únicos, cantidades no negativas, precios numéricos y
   `precio_venta` en `1/2/3` antes de escribir.
7. Calcular revisiones SHA-256 canónicas después del commit lógico:
   `configuration_revision`, `manifest_revision`, `catalog_revision` y una
   `stock_revision` por tienda.

Respuesta `200/201`:

```json
{
  "success": true,
  "idempotency_key": "8c2b5604-bca7-43a5-bd31-fd0740c03fb2",
  "configuration": {
    "revision": "<sha256>",
    "changed": true,
    "identity": {"username": "operador", "id_cotizador": "COT01"},
    "settings": {
      "listing_type": "AMBOS",
      "allow_no_stock": false,
      "telemarketing": false,
      "enable_ai": false,
      "enable_recommendations": true
    },
    "default_scope": {
      "country_code": "PY",
      "company_type": "EF PERFUMES"
    },
    "default_store_id": "ASU01"
  },
  "catalog_stock": {
    "manifest_revision": "<sha256>",
    "groups": []
  }
}
```

El bloque `catalog_stock` debe seguir exactamente el contrato ya consumido por
`getCotizadorCatalogStock`; puede omitirse en el bootstrap y entregarse en la
primera sincronización periódica.

## 2. Cambios del servidor hacia el equipo

El request actual de `POST /service/db/getCotizadorCatalogStock` acepta además:

```json
{"configuration_revision": "<sha256-local>"}
```

La respuesta conserva `manifest_revision/groups` y añade `configuration`:

- Si la revisión no cambió: `revision=<misma>`, `changed=false`; no necesita
  reenviar `settings`.
- Si cambió: `revision=<nueva>`, `changed=true` y snapshot completo de settings,
  `default_scope` y `default_store_id`.
- Las asignaciones, catálogos y stocks continúan viajando en `groups/stores` y
  sus revisiones. Así una cuenta puede recibir simultáneamente varios países,
  empresas y tiendas sin aplanarlos a un único registro local.

El cliente aplica configuración y manifiesto en una sola transacción SQLite. Si
un bloque es inválido, conserva la revisión y los snapshots anteriores. Los
cambios globales quedan persistidos inmediatamente y la interfaz solicita un
reinicio para consumidores legacy que todavía capturan constantes al importar.

## 3. Autoridad y seguridad

Son autoritativos en servidor: identidad funcional, telemarketing, preferencias
de catálogo/cotización, ámbito predeterminado, asignaciones, catálogo y stock.

Permanecen locales por equipo: rutas, nivel/directorio de log, URL/flags del
updater, IP/puerto de impresora, tema visual y secretos/derivados de autenticación.
El servidor no debe aceptar ni devolver hashes, contraseñas o tokens dentro de
la configuración funcional.

Toda consulta y mutación debe comprobar que el token puede administrar el
`username/id_cotizador/pid` solicitado. Los logs del servidor deben registrar
IDs técnicos e idempotencia, sin copiar clientes, documentos ni cuerpos completos
de inventario.
