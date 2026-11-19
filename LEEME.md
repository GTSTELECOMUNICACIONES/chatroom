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

## El contrato con el portal (versión 0.2, REVISADO POR CARLOS el 19-11-2026)

Lo acordado el 19-11-2026, con los huecos rellenados por Odoo. **Lo marcado con
⚠ lo he decidido yo para poder escribir el código, y el portal tiene que
confirmarlo o corregirlo.** Todo lo que lee el evento está en una sola función,
`_alma_leer_evento`, para que un cambio de nombre se toque en un sitio.

### Portal → Odoo: `POST <URL de entrada>`

La URL de entrada sale en la ficha de la pasarela (`/gateway/alma/<clave>/update`).

Firma (confirmada): `X-Alma-Ts: <segundos unix>` y
`X-Alma-Firma: sha256=<hex>`, donde el hex es HMAC-SHA256 con el secreto de la
pasarela sobre `<ts>.<cuerpo crudo>` —el crudo, no el JSON reserializado—.
Fuera de ±5 minutos se rechaza.

```json
{
  "tipo": "mensaje | acuse | estado",
  "emitido_en": "2026-11-19T10:00:00Z",
  "numero": "<número emisor>",
  "conversacion": {"id": "...", "contacto": {"telefono": "...", "nombre": "..."}},
  "mensaje": {"id": "...", "de": "contacto | ia | operador | movil | sistema",
              "texto": "...",
              "adjuntos": [{"nombre": "...", "mime": "...", "url": "... o null"}]},
  "acuse": {"mensaje_id": "...", "estado": "en_cola | salio | entregado | leido | fallido",
            "alma_motivo": "...", "detalle": "..."},
  "estado": {"relevo": "ia | humano | resuelta", "asignado_a": "<correo> o null",
             "espera": true, "motivo_relevo": "..."}
}
```

- `numero` tiene que ser el de la pasarela; si no, se descarta.
- `mensaje.de`, cinco valores: `contacto` es el cliente; `ia` y `sistema`
  salen firmados como «Alma (IA)»; `operador` (desde el ChatRoom del portal) y
  `movil` (el dueño desde su propio WhatsApp) salen como la empresa. El valor
  se guarda en `mail.message.alma_origen`: agrupar para enseñar es de Odoo,
  pero la información no se pierde.
- **Adjuntos: hoy el portal no guarda los ficheros y la `url` llega `null`**
  (Meta los borra a los 7 días). Odoo enseña «Se recibió una imagen, pero el
  fichero no está disponible». Cuando el portal los guarde, la `url` será suya
  y se descargan con el mismo `Bearer`, hasta 25 MB.
- **La cola se deriva en Odoo de las piezas**, nunca la manda hecha el portal:
  `esperando` = humano, sin asignar y `espera`; `contestada` = humano, sin
  asignar y sin `espera` (el dueño contestó desde el móvil antes de que nadie
  la cogiera: el caso que con cuatro etiquetas se perdía).
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

Devuelve `{"eventos": [ ... ]}`, cada uno con la misma forma que la entrada
(confirmado). `desde` se compara con `emitido_en`, el reloj del portal al
emitir, y no con la fecha del mensaje. Odoo pide desde cinco minutos antes del
último evento que procesó y ordena por `emitido_en`.

### Lo que falta

- La vuelta del relevo: «una persona coge esto» / «se la devuelvo a la IA».
- Enviar adjuntos y plantillas.
- La pantalla de la cola con Atender / Terminar (hoy es una lista).
