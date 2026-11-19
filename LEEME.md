# ChatRoom de GTS

Módulo de Odoo para atender WhatsApp desde Odoo, contra el portal de Alma.

**Arquitectura decidida el 19-11-2026** (ver la memoria de Óscar,
`chatroom-19-arranque.md`, que tiene el traspaso completo):

- Se construye **sobre el `mail_gateway` de la OCA** (`github.com/OCA/mail`,
  rama 19.0), **por dependencia y no copiándolo**. Es un sistema de enchufes:
  `gateway_type` es una Selection vacía y el base resuelve
  `self.env[f"mail.gateway.{gateway_type}"]`. Añadimos el tipo `alma` sin
  tocar nada suyo, así que sus actualizaciones no pueden pisarnos.
- **Licencia AGPL-3**, heredada de ellos.
- **El repositorio es PÚBLICO.** La dirección del portal y las claves van en
  campos de configuración, **nunca en el código**: así un módulo que se lleven
  no sirve de nada sin saber contra qué conecta.

## Antes de subir

    ~/venv/bin/python tools/sin_secretos.py

Impide subir la dirección del portal, claves, secretos escritos a mano y
teléfonos españoles reales. En un repositorio público un descuido no se
arregla borrando el commit: para cuando se ve, ya está copiado e indexado.

## El contrato con el portal (versión 0.1, PENDIENTE DE CONFIRMAR CON CARLOS)

Lo acordado el 19-11-2026, con los huecos rellenados por Odoo. **Lo marcado con
⚠ lo he decidido yo para poder escribir el código, y el portal tiene que
confirmarlo o corregirlo.** Todo lo que lee el evento está en una sola función,
`_alma_leer_evento`, para que un cambio de nombre se toque en un sitio.

### Portal → Odoo: `POST <URL de entrada>`

La URL de entrada sale en la ficha de la pasarela (`/gateway/alma/<clave>/update`).

⚠ Firma: `X-Alma-Ts: <segundos unix>` y
`X-Alma-Firma: sha256=<hex>`, donde el hex es HMAC-SHA256 con el secreto de la
pasarela sobre `<ts>.<cuerpo crudo>`. Fuera de ±5 minutos se rechaza.

```json
{
  "tipo": "mensaje | acuse | estado",
  "emitido_en": "2026-11-19T10:00:00Z",
  "numero": "<número emisor>",
  "conversacion": {"id": "...", "contacto": {"telefono": "...", "nombre": "..."}},
  "mensaje": {"id": "...", "de": "cliente | ia | operador", "texto": "...",
              "adjuntos": [{"nombre": "...", "mime": "...", "url": "..."}]},
  "acuse": {"mensaje_id": "...", "estado": "en_cola | salio | entregado | leido | fallido",
            "alma_motivo": "...", "detalle": "..."},
  "estado": {"relevo": "ia | esperando | humano | resuelta", "asignado_a": "..."}
}
```

- `numero` tiene que ser el de la pasarela; si no, se descarta.
- ⚠ `mensaje.de`: `ia` sale firmado como «Alma (IA)» y `operador` (el dueño
  desde su móvil) como la empresa.
- ⚠ Los adjuntos se descargan de su `url` con el mismo `Bearer`. Máximo 25 MB.
- Los repetidos se descartan por `mensaje.id`. El estado de un evento más
  viejo no pisa al de uno más nuevo.

### Odoo → portal: `POST <URL del portal>/api/odoo/enviar`

`Authorization: Bearer <token>` e `Idempotency-Key: odoo-<uuid de la base>-<id>`.

```json
{"numero": "...", "conversacion_id": "... o null", "a": "<teléfono>",
 "texto": "...", "operador": "<nombre de quien escribe>"}
```

Respuesta `202 {mensaje_id, conversacion_id, estado}`. Un error con
`alma_motivo` se enseña en el mensaje con su texto en castellano.
⚠ **Los adjuntos todavía no salen**: el mensaje falla a la vista en vez de
salir sin el fichero.

### Odoo → portal: `GET <URL del portal>/api/odoo/desde?numero=...&desde=<ISO>`

⚠ Devuelve `{"eventos": [ ... ]}`, cada uno con la misma forma que la entrada.
Odoo pide desde cinco minutos antes del último evento que procesó.

### Lo que falta

- La vuelta del relevo: «una persona coge esto» / «se la devuelvo a la IA».
- Enviar adjuntos y plantillas.
- La pantalla de la cola con Atender / Terminar (hoy es una lista).
