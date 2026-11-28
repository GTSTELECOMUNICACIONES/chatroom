# ChatRoom de GTS

Módulo de Odoo para atender WhatsApp desde Odoo, contra el portal de Alma.

## Rama 18.0 (Odoo 18 Community)

Portada el 28-11-2026 desde la 19.0.0.19.1 (encargo de David, para las demos
de despachos y asesorías en la 18). Misma numeración tras «18.0.». Lo que
cambia respecto a la 19, todo comprobado contra `~/ref/odoo-18.0`:

- **`mail_gateway` está en `OCA/social`** (rama 18.0), no en `OCA/mail`. El
  servidor necesita `requests_toolbelt`, igual que en la 19.
- **Depende también de `sms`**: en la 18 es `sms` quien da a los contactos
  `phone_sanitized` (`sms/models/res_partner.py` hereda `mail.thread.phone`);
  en la 19 lo da `phone_validation`. `sms` es auto_install de `mail`.
- **Dos teléfonos en el contacto (`phone` y `mobile`)**. Buscar un contacto
  por número mira los dos (`res.partner._gts_buscar_por_telefono`): el
  `phone_sanitized` de Odoo guarda solo uno. Los contactos que crea el
  ChatRoom llevan el número en `mobile`, y la plantilla toma el móvil por
  defecto.
- API: `_sql_constraints`, `groups_id`, `_to_store(store)` con `store.add`,
  `_bus_send_store`, `Record.one/attr` en el JS, acciones de cabecera que
  reciben el componente, el selector de campos propio (`gts_field_selector`;
  el `field_selector` de Odoo es de la 19) y `timezone.utc` (la 18 admite
  Python 3.10).
- Sin `migrations/`: en la 18 se instala de cero.

Antes de subir, `tools/comprobar.sh` (el hook de pre-push) pasa además
`tools/comprobar_api_de_rama.py` (API de la 19 que en la 18 no existe) y
`tools/comprobar_js_contra_fuente.py` (imports, parches y xpath de las
plantillas OWL contra el fuente de la 18: si no casan, el backend se queda en
blanco y ningún linter lo ve).

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

## Probar sin el portal: `tools/simular_portal.py`

Hace de portal contra la URL de entrada de una pasarela: construye los eventos
con la forma exacta del contrato de abajo, los firma igual que espera el
controlador y los manda. **La URL y el secreto van por variable de entorno,
nunca en el fichero.** Escenarios: `cliente`, `ia`, `escala`, `movil`,
`estado`, `acuse`, `repetido`, `eco`, `viejo` y `recorrido` (todos seguidos).
`--solo-imprimir` enseña la petición sin mandarla; `--firma-mala` y
`--desfase 400` tienen que ser rechazadas.

    export ALMA_URL_ENTRADA='<la URL de entrada de la ficha>'
    export ALMA_SECRETO='<el secreto de la ficha>'
    export ALMA_NUMERO=15555550100        # el número de la pasarela
    python3 tools/simular_portal.py recorrido

## El bucle de eco del 21-11-2026 (arreglado en 0.3.7)

**Qué pasó.** David contestó desde Odoo; el POST a `/api/odoo/enviar` iba
DENTRO de la transacción del mensaje, y el portal mandaba el eco por el
webhook ANTES de contestar el 202. El eco se publicaba (sin reenviarse: ya
llevaba `no_gateway_notification`) y escribía la fila del canal; la
transacción de David chocaba con ella al terminar (`SerializationFailure`) y
Odoo **reintentaba la petición entera** (`odoo/service/model.py`, `retrying`,
hasta 5 veces con espera aleatoria creciente): deshacía el mensaje en la base,
pero el POST ya había salido, y lo repetía con OTRA notificación, OTRA
`Idempotency-Key` y otro WhatsApp. Por eso el mensaje de David no está en la
base y sí sus ecos, y por eso faltan ids de mensaje y de notificación.

**Las defensas, independientes:**

1. **El envío va en `cr.postcommit`**, como los webhooks del propio Odoo:
   sale una vez y solo si el mensaje ya está guardado; un rollback lo cancela.
   El resultado se escribe en un cursor nuevo, y si choca con el webhook se
   reintenta ESCRIBIR, nunca enviar.
2. **Lo que entra del portal no sale nunca**: se publica con
   `no_gateway_notification` y `alma_entrante`, y `_send` corta cualquier
   mensaje con `alma_origen` o `alma_mensaje_id` (queda «cancelado» a la vista).
3. **El eco de lo que mandó Odoo se reconoce** por el id: el portal devuelve
   en `mensaje.id` el mismo `mensaje_id` del 202 (Carlos), guardado en
   `mail.notification.gateway_message_id`. Puede llegar antes de que esté
   guardado; entonces se vería dos veces, pero no sale (defensa 2). Los
   **acuses** con ese id siguen llegando a su notificación.
4. **Una pasarela desintegrada no envía**: es el freno de mano y corta las
   dos direcciones (el 21-11 salió un mensaje después de desintegrarla).
5. Del portal (Carlos): ya no reenvía a Odoo lo que entra por `/enviar`, y
   frena con **429 `posible_bucle`** el mismo texto al mismo destinatario 3
   veces en 5 minutos.

**Comprobarlo sin el portal:** con la pasarela integrada y el teléfono
FICTICIO del simulador (nunca uno real: si fallara, saldría un WhatsApp),

    python3 tools/simular_portal.py eco

y en Odoo: el mensaje sale como la empresa, origen «Equipo, desde el ChatRoom»,
y **no tiene notificación de pasarela** (`mail.notification` con
`notification_type = gateway` y ese mensaje: cero); en el log no aparece
ninguna llamada a `/api/odoo/enviar`. Con `--mensaje-id <gateway_message_id>`
de un envío de Odoo, además no aparece dos veces. Las pruebas unitarias están
en `gts_chatroom/tests/test_eco.py`.

**Odoo no sigue redirecciones del portal (0.3.5).** Medido el 21-11-2026: las
rutas `/api/odoo/*` de la dirección configurada contestaban `307` a `/login`
(no llegaban al código del portal) y Odoo, siguiéndola, leía la página de
login como «ningún evento»: «Recuperados 0 eventos» sin error. Ahora una
respuesta que no sea 2xx con JSON es un error a la vista (en «Recuperar», en
el mensaje que sale, en el relevo) y en el log del cron.

**El controlador de la OCA contesta `200 {}` también cuando rechaza la firma**
(o no encuentra la pasarela, o no está integrada): un 200 no prueba que haya
entrado. Se mira en Odoo, o en el log («could not be verified»).

## La cola («Conversaciones» y «Esperando»)

- **Conversaciones**: kanban agrupado por la cola derivada, en este orden:
  Esperando, Contestada sin asignar, Asignada, Con la IA, Resuelta. «Esperando»
  sale arriba siempre, también vacía. La tarjeta abre la conversación en
  Discuss. No se arrastra entre columnas: la cola se cambia con los botones.
- **Esperando (N)**: solo las que esperan, la que más lleva arriba. El número
  lo pone el servidor al cargar los menús: **se actualiza al recargar la
  página, no en vivo** (en vivo necesita un componente en la barra superior, y
  un fallo ahí deja el backend en blanco; ya pasó una vez).
- **Atender**: me la quedo. Relevo a una persona, asignada a mi correo (o a mi
  usuario si no tengo correo), y abre la conversación.
- **Devolver a la IA** y **Resuelta**. «Resuelta» **se niega si el último
  mensaje es del cliente**: esconder una conversación sin contestar la vuelve
  invisible. Se contesta, o se devuelve a la IA.
- **Los tres botones avisan primero al portal** (`/api/odoo/relevo`). Si el
  portal no contesta o dice que no, **la conversación no cambia en Odoo** y se
  enseña el motivo: la copia de Odoo nunca se adelanta al portal. Si lo acepta,
  queda una línea en la conversación («Ana atiende la conversación»).
- **Aviso al entrar en espera (0.3.8)**: en el momento en que una conversación
  pasa a «Esperando», un aviso emergente fijo («WhatsApp: alguien espera») a
  los miembros de la pasarela (o al responsable si no hay). Es el
  `simple_notification` del bus de Odoo: **sin JavaScript nuestro**. Sale una
  vez al entrar, no con cada evento. Sin sonido. Solo lo ve quien tenga Odoo
  abierto en ese momento; para lo demás está lo de abajo.
- **Aviso cuando nadie la coge**: pasados N minutos en «Esperando» (5 por
  defecto; Ajustes → ChatRoom, con el responsable), una **actividad** al
  responsable —el administrador si no hay ninguno—. Es un registro que se
  queda, no una notificación que pasa. Va en la **ficha del contacto**, con el
  enlace a la conversación: en un canal de Discuss no se puede, porque crearla
  intenta añadir al responsable como seguidor y Discuss lo prohíbe. Se cierra
  sola cuando la conversación sale de «Esperando». La dispara la propia
  conversación al entrar en espera; un cron cada 15 minutos hace de red.

## El contrato con el portal (versión 0.3, REVISADO POR CARLOS el 21-11-2026)

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
- **`mensaje.cuando`** (ISO; confirmado por Carlos el 21-11-2026): la hora real
  del mensaje. Hace falta para lo recuperado por `/desde`, donde todos los eventos
  de una respuesta traen el mismo `emitido_en`; sin ella salen en Discuss con
  la hora de la recuperación. Si no viene, se usa `emitido_en`.

### Odoo → portal: `POST <URL del portal>/api/odoo/enviar`

`Authorization: Bearer <token>` e `Idempotency-Key: odoo-<uuid de la base>-<id>`.

```json
{"numero": "...", "conversacion_id": "... o null", "a": "<teléfono>",
 "texto": "...", "operador": "<nombre de quien escribe>"}
```

**Existe en el portal (Carlos, 21-11-2026).** Respuesta
`202 {mensaje_id, estado: "en_cola"}`; `conversacion_id` **puede no venir**.
La misma `Idempotency-Key` repetida devuelve lo de la primera vez con
`repetido: true` y no envía otra vez: Odoo lo trata como éxito y no duplica
nada. `operador` se manda aunque el portal todavía no lo guarde.

- Un error con `alma_motivo` se enseña en el mensaje con su texto en
  castellano. `numero_desconocido` (404): «El número de WhatsApp configurado en
  la pasarela no pertenece a la cuenta del portal de Alma. Revisa la
  configuración.»
- **401 seco**, sin cuerpo útil (clave revocada, inexistente o ausente): «El
  portal de Alma no acepta la clave de esta pasarela. Revisa la clave en la
  configuración.» Vale para todas las llamadas.
- **Odoo no reintenta nada solo.** El portal todavía no se protege de
  duplicados y un timeout no dice si el mensaje salió: el envío fallido se
  queda en «fallido» a la vista y lo reenvía una persona.
- ⚠ **Los adjuntos todavía no salen**: el mensaje falla a la vista en vez de
  salir sin el fichero.

### Odoo → portal: `POST <URL del portal>/api/odoo/relevo` (confirmado por Carlos el 21-11-2026)

Lo mandan los botones Atender, Devolver a la IA y Resuelta. `Authorization:
Bearer <token>` e `Idempotency-Key: odoo-<uuid de la base>-relevo-<id del
canal>-<uuid>` (una por pulsación). El cuerpo se construye solo en
`_alma_cuerpo_relevo`:

```json
{"numero": "...", "conversacion_id": "...", "relevo": "humano | ia | resuelta",
 "asignado_a": "<correo> o null", "operador": "<nombre de quien pulsa>"}
```

`asignado_a` solo va con `humano`: el correo del usuario, o su usuario de
entrada si no tiene correo. Sin `conversacion_id` (canal que nunca recibió
uno) el botón no llama y lo explica.

- **Respuesta**: ⚠ **lo propone Odoo**: cualquier 2xx es «aceptado». Si trae
  `{"estado": {relevo, asignado_a, espera, motivo_relevo}, "emitido_en": ...}`
  se copia eso (manda el portal); si no, se copia lo que se pidió. Como el
  portal acaba de aceptarlo, no se descarta por «viejo» aunque los relojes no
  casen.
- **Operador que no es miembro de la cuenta**: con `relevo: "humano"` y un
  `asignado_a` que el portal no conoce, el error lleva dentro el correo literal
  (el caso del primer día, con todos los operadores). Se enseña «<correo> no
  está dado de alta en el portal de Alma. Pide que te den de alta y vuelve a
  intentarlo.» ⚠ **La forma exacta de ese error no está cerrada**: el correo se
  busca en `detalle` y en `alma_motivo`; si no aparece, se enseña el error tal
  cual. Todo en `_alma_texto_error_relevo`.
- Cualquier error: **la conversación no cambia en Odoo** y la persona ve el
  motivo.

### Odoo → portal: `GET <URL del portal>/api/odoo/desde?numero=...&desde=<ISO>`

**Existe en el portal (Carlos, 21-11-2026).** Devuelve
`{"eventos": [...], "truncado": bool, "hasta": "<ISO>"}`, cada evento con la
forma de la entrada y su bloque de estado. Con `truncado: true` (tope de 500)
Odoo vuelve a pedir desde `hasta`, como mucho 10 vueltas; lo que quede, en la
siguiente pasada.

- **`mensaje.estado`** (Carlos, 21-11-2026): cada mensaje de `/desde` trae su
  último estado (en_cola, salio, entregado, leido, fallido), **también los que
  mandó Odoo**. Uno nuestro no se publica otra vez, pero su estado se aplica a
  su notificación: una recuperación desatasca un envío cuyo acuse se perdió.
- **No se reordena.** Dentro de una respuesta todos los eventos llevan el
  mismo `emitido_en` (el reloj del portal al emitirla) y ya vienen por fecha
  del mensaje: se procesan en el orden en que llegan. `emitido_en` solo decide
  si un bloque de estado es más nuevo que el guardado.
- ⚠ Eso supone que **el bloque de estado de `/desde` es el estado ACTUAL** de
  la conversación y no el que tenía cuando se escribió cada mensaje: si fuera
  el antiguo, al llevar un `emitido_en` reciente pisaría un relevo más nuevo.
  Carlos tiene que confirmarlo.
- Los adjuntos llegan como `{nombre: null, mime: "image", url: null}`: se
  tratan igual que los del webhook.
- **Un cron cada 5 minutos** recorre las pasarelas `alma` integradas y pide
  desde cinco minutos antes del último evento procesado: el portal no reintenta
  el reenvío, así que lo que no llegó por el webhook se recoge aquí. Timeout de
  10 s por petición, sin reintentos, y una pasarela que falla se registra y no
  para a las demás. El botón «Recuperar del portal» de la ficha sigue.

### Lo que falta

**Del lado del portal (Carlos, 21-11-2026): todavía no existen `/relevo` ni el
reenvío firmado.** `/enviar` y `/desde` sí. Hasta que exista el reenvío, la
entrada se prueba con `tools/simular_portal.py`; hasta que exista `/relevo`,
los botones de la cola darán error (a la vista, y sin cambiar nada).

- **Nada de 0.3.0 se ha probado en un Odoo**: ni instalado, ni la pantalla, ni
  el cron. Ver «Sin probar» abajo.
- Confirmado por Carlos el 21-11-2026: `/relevo` devuelve SIEMPRE el estado
  resultante (también con error, y Odoo lo copia), la clave de idempotencia,
  `mensaje.cuando` y que el estado de `/desde` es el actual. «Resuelta» con el
  último mensaje del cliente se AVISA, no se impide (los dos lados igual).
  Operador no dado de alta: 409 `operador_no_es_miembro`, el correo solo en
  `detalle`, y el relevo SE HACE (humano, sin asignar) con su estado.
- Aviso que se ve y suena: la pestaña parpadeando hasta que alguien la coge y
  el sonido **solo cuando el contador sube**. Es JavaScript y va con cuidado
  (un fallo en la barra superior deja el backend en blanco).
- Si la IA promete avisar a alguien, escalar: es regla del portal, pero Odoo
  podría detectar la frase y avisar.
- Enviar adjuntos y plantillas.
- Que el webhook y el cron de `/desde` procesen a la vez el mismo mensaje
  podría duplicarlo (los dos miran «¿existe ya?» antes de que el otro guarde).
  Es una ventana de milisegundos; si se ve, un índice único sobre
  `alma_mensaje_id` por canal lo cierra.

### Sin probar (no hay Odoo en la máquina donde se escribe)

- La instalación entera de 0.3.0 (pasan los comprobadores, que no son
  instalar).
- El kanban: el clic en la tarjeta que abre Discuss, los botones con
  `invisible`, el orden de las columnas con `group_expand`.
- El contador del menú (`load_web_menus`) y que el navegador lo refresque.
- La pantalla de Ajustes.
- El cron de avisos con `_trigger`, y la actividad en la ficha del contacto.
- El cron de `/desde` contra el portal de verdad.
- **Sacar la descarga de adjuntos del webhook** antes de que el portal guarde
  ficheros: el portal corta el reenvío a los 3,5 s (Carlos, 21-11-2026) y la
  descarga va hoy dentro. No se pierde nada (lo recoge `/desde`), pero se pierde
  la inmediatez.

