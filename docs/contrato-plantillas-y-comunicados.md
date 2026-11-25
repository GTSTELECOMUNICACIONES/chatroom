# Contrato del portal: plantillas y comunicados (BORRADOR)

Borrador de Carlos del 25-11-2026. Autenticación igual que `/enviar`
(`Authorization: Bearer <clave del conector>`). Todas comprueban que el
número sea de la cuenta de esa clave. Orden en que lo construye el portal:
1 → 3 → 2 → 4.

## 1 · GET /api/odoo/plantillas?numero=…

Cada plantilla trae `id` (el que se manda al enviar), `nombre`, `idioma`,
`categoria` (la que Meta APLICA: usar siempre esta), `categoria_declarada`,
`estado` (aprobada | pendiente | rechazada | pausada), `motivo` (literal de
Meta), `cuerpo` con `{{n}}`, `cabecera`, `botones`, `variables` (cuántas en
cuerpo, cabecera y botón), `se_puede_enviar` y `por_que_no`.

**`se_puede_enviar` se respeta:** hoy el portal bloquea las plantillas con
variable en cabecera o en botón. Se enseñan, no se dejan mandar, y
`por_que_no` se pinta tal cual.

## 2 · POST /api/odoo/plantillas

`numero, nombre, idioma, categoria, cuerpo, cabecera, pie, botones, ejemplos`.
**Un ejemplo por variable, obligatorio** (Meta los exige). 202 `{id, estado:
pendiente}`; 400 `plantilla_invalida` con el error en castellano. El cambio
de estado llega por `/desde`, no por webhook. El portal aún no procesa los
avisos de Meta sobre plantillas: está por construir.

## 3 · POST /api/odoo/enviar con plantilla

`{numero, a, conversacion_id|null, plantilla: {id, parametros: [...]},
operador}` → 202 `{mensaje_id, estado: en_cola}`; 402 `sin_saldo`; 409
`baja_marketing`; 409 `bloqueado`.

La baja se mira **según la categoría**: una de marketing no sale a quien tiene
la baja; una de utilidad sí. `bloqueado` corta las dos. Corta por saldo.

**Verificado solo con parámetros de CUERPO.** El parámetro de un botón URL no
se ha conseguido nunca: no dar por hecho que funciona hasta que Carlos lo vea
salir.

## 4 · POST /api/odoo/enviar/lote

`{numero, plantilla, destinatarios: [{a, parametros}]}` → 202 `{lote_id,
aceptados, rechazados: [{a, alma_motivo}]}`. Los rechazados vuelven EN EL
ACTO, antes de mandar nada: se enseñan en la vista previa. El ritmo lo pone
el portal (conoce los límites del proveedor): Odoo NO envía de uno en uno.
Tope 1.000 por lote; el saldo se comprueba para el lote entero antes de
empezar. Lo que sale llega por los acuses de siempre. `GET
/api/odoo/lote/<id>` da el estado agregado.

## Estado y rechazos (Carlos, 25-11-2026, tarde)

Las rutas 1 y 3 están en producción del portal. Rechazos de `/enviar` con
plantilla:

| Código | Motivo | Qué es |
|---|---|---|
| 402 | `trial_caducado`, `sin_saldo` | La cuenta está cortada: el comunicado entero no sale |
| 409 | `bloqueado` | Contacto bloqueado |
| 409 | `baja_marketing` | Baja de comerciales; SOLO con plantilla de marketing |
| 409 | `plantilla_no_soportada` | Variable en cabecera o en botón |
| 404 | `plantilla_no_encontrada` | No está en el catálogo de ese número |
| 400 | `parametros_incorrectos` | Sobran o faltan; cuenta los del CUERPO (`variables.cuerpo`) |
| 429 | `posible_bucle` | El mismo mensaje montado a la misma persona 3 veces en 5 min |

`baja_marketing` NO se cachea por contacto: el mismo contacto puede ser
rechazado para un comunicado de marketing y aceptado para uno de utilidad.
El catálogo no llama al proveedor (Gupshup: 10 peticiones/min por app): es la
copia del portal; `aviso` dice si está vacío o sin copiar.
