"""El transporte entre Odoo y el portal de Alma.

EL CONTRATO, en tres flujos y ninguno en el camino critico del otro:

    portal -> Odoo   cada evento (mensaje, acuse), CON el estado del relevo
    Odoo -> portal   POST /api/odoo/enviar, lo que escribe una persona aqui
    Odoo -> portal   POST /api/odoo/relevo, «la cojo yo» / «para la IA» / «resuelta»
    Odoo -> portal   GET  /api/odoo/desde, para recuperar lo que se perdio

La forma exacta de cada evento esta en `_alma_leer_evento`, y SOLO ahi: si el
portal cambia un nombre, se toca una funcion y no todo el fichero.
"""

import hashlib
import hmac
import logging
import re
import time
import uuid
from datetime import UTC, datetime, timedelta

import requests
from markupsafe import Markup

from odoo import api, models, modules
from odoo.http import request
from odoo.tools import html2plaintext, plaintext2html

from odoo.exceptions import UserError

from odoo.modules.registry import Registry
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from odoo.addons.base.models.ir_mail_server import MailDeliveryException

from .mail_message import ORIGENES

_logger = logging.getLogger(__name__)

# Margen para la hora de la firma. El mismo que se acordo para la pantalla.
MARGEN_FIRMA = 300
TIMEOUT = 10
# Un adjunto mas grande no se descarga: se deja una linea diciendolo. Un
# fichero de 200 MB metido en la transaccion del webhook la tumba.
MAX_ADJUNTO = 25 * 1024 * 1024
# Vueltas de /desde con `truncado`: 10 x 500 eventos. Mas, en la siguiente
# pasada del cron: un hilo de cron colgado es un recurso escaso perdido.
MAX_VUELTAS = 10
# Intentos de ESCRIBIR el resultado de un envio si choca con el webhook. El
# envio en si no se repite nunca.
MAX_ESCRITURAS = 3

MOTIVOS = {
    "sin_saldo": "No queda saldo de WhatsApp en la cuenta. Hay que recargarla "
    "para seguir enviando.",
    "bloqueado": "El contacto ha bloqueado este numero.",
    "baja_marketing": "El contacto se ha dado de baja de los mensajes " "comerciales.",
    "ventana_cerrada": "Han pasado mas de 24 horas desde el ultimo mensaje del "
    "cliente: WhatsApp solo deja enviarle una plantilla aprobada.",
    "plantilla_no_aprobada": "La plantilla no esta aprobada por Meta.",
    # Este es NUESTRO y no puede sonar a culpa del cliente.
    "saldo_del_servicio": "Incidencia temporal del servicio de mensajeria. No "
    "es un problema de su cuenta: avise a soporte de GTS.",
    # Textos de Carlos, 21-11-2026.
    "numero_desconocido": "El número de WhatsApp configurado en la pasarela no "
    "pertenece a la cuenta del portal de Alma. Revisa la configuración.",
    # 429 de /enviar: mismo numero, destinatario y texto 3 veces en 5 minutos
    # (freno del portal tras el bucle del 21-11-2026).
    "posible_bucle": "El portal ha frenado el envío: el mismo mensaje ha salido "
    "tres veces en cinco minutos y parece un bucle. Revisa la conversación "
    "antes de volver a enviarlo.",
}

# Un correo dentro de un texto de error. Admite letras con tilde: el ejemplo de
# Carlos es «rubén@...».
CORREO = re.compile(r"[^\s@<>\"'(),;:]+@[^\s@<>\"'(),;:]+\.[^\s@<>\"'(),;:.]+")


def _digitos(numero):
    return "".join(c for c in str(numero or "") if c.isdigit())


def _fecha(valor):
    """ISO 8601 del portal -> datetime UTC sin zona, que es como guarda Odoo.
    Sin fecha, ahora: un evento sin fecha no se tira."""
    if not valor:
        return datetime.now(UTC).replace(tzinfo=None)
    fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    if fecha.tzinfo:
        fecha = fecha.astimezone(UTC).replace(tzinfo=None)
    return fecha


class MailGatewayAlma(models.AbstractModel):
    _inherit = "mail.gateway.abstract"
    _name = "mail.gateway.alma"
    _description = "Pasarela del portal de Alma"

    # ------------------------------------------------------------------
    # Entrada
    # ------------------------------------------------------------------

    def _verify_update(self, bot_data, kwargs):
        """Firma HMAC-SHA256 de `<ts>.<cuerpo>` con el secreto de la pasarela.

        La hora va DENTRO de la firma: si no, un mensaje capturado se puede
        volver a mandar cuando se quiera. Fuera del margen se rechaza."""
        cabeceras = request.httprequest.headers
        firma = cabeceras.get("X-Alma-Firma", "")
        ts = cabeceras.get("X-Alma-Ts", "")
        secreto = bot_data.get("webhook_secret")
        if not (firma and ts and secreto):
            return False
        try:
            if abs(time.time() - int(ts)) > MARGEN_FIRMA:
                return False
        except ValueError:
            return False
        esperada = hmac.new(
            secreto.encode(),
            ts.encode() + b"." + request.httprequest.get_data(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(f"sha256={esperada}", firma)

    def _receive_update(self, gateway, update):
        if update:
            self._alma_procesar(gateway, update)

    def _alma_procesar(self, gateway, update):
        evento = self._alma_leer_evento(update)
        # El portal identifica la cuenta por el numero emisor. Si no casa con
        # el de esta pasarela, es un error de configuracion de alguien, y lo
        # unico seguro es no meterlo en la conversacion de otro.
        if gateway.alma_numero and evento["numero"] != _digitos(gateway.alma_numero):
            _logger.warning(
                "ChatRoom: evento para el numero %s en la pasarela %s, descartado",
                evento["numero"],
                gateway.id,
            )
            return False
        if evento["tipo"] == "acuse":
            self._alma_acuse(gateway, evento)
        elif evento["tipo"] == "mensaje":
            self._alma_mensaje(gateway, evento)
        else:
            canal = self.env["discuss.channel"].browse(
                gateway._get_channel_id(evento["telefono"])
            )
            if canal and evento["estado"]:
                canal._alma_aplicar_estado(evento["estado"], evento["emitido_en"])
        if (
            not gateway.alma_ultimo_evento
            or evento["emitido_en"] > gateway.alma_ultimo_evento
        ):
            gateway.sudo().alma_ultimo_evento = evento["emitido_en"]
        return True

    def _alma_leer_evento(self, update):
        """La forma del evento, en un solo sitio.

        {tipo, emitido_en, numero,
         conversacion: {id, contacto: {telefono, nombre}},
         mensaje: {id, de: contacto|ia|operador|movil|sistema, texto,
                   cuando (la hora real del mensaje, ISO),
                   adjuntos: [{nombre o null, mime, url o null}]},
         acuse: {mensaje_id, estado, alma_motivo, detalle},
         estado: {relevo: ia|humano|resuelta, asignado_a, espera,
                  motivo_relevo}}
        """
        conversacion = update.get("conversacion") or {}
        contacto = conversacion.get("contacto") or {}
        mensaje = update.get("mensaje") or {}
        emitido_en = _fecha(update.get("emitido_en"))
        return {
            "tipo": update.get("tipo"),
            "emitido_en": emitido_en,
            # La hora DEL MENSAJE, `cuando` (confirmado por Carlos, 21-11). En
            # /desde todos los eventos de una respuesta llevan el mismo
            # `emitido_en`: sin esto, lo recuperado saldria en Discuss con la
            # hora de la recuperacion. Si no viene, `emitido_en`.
            "fecha_mensaje": (
                _fecha(mensaje["cuando"]) if mensaje.get("cuando") else emitido_en
            ),
            "numero": _digitos(update.get("numero")),
            "conversacion_id": conversacion.get("id") and str(conversacion["id"]),
            "telefono": _digitos(contacto.get("telefono")),
            "nombre": contacto.get("nombre"),
            "mensaje": mensaje,
            "acuse": update.get("acuse") or {},
            "estado": update.get("estado") or {},
        }

    def _alma_mensaje(self, gateway, evento):
        if not evento["telefono"]:
            return
        canal = self._get_channel(
            gateway, evento["telefono"], evento, force_create=True
        )
        if not canal:
            return
        mensaje = evento["mensaje"]
        if evento["conversacion_id"] and not canal.alma_conversacion_id:
            canal.sudo().alma_conversacion_id = evento["conversacion_id"]
        if evento["estado"]:
            canal._alma_aplicar_estado(evento["estado"], evento["emitido_en"])
        alma_id = mensaje.get("id") and str(mensaje["id"])
        if alma_id and mensaje.get("estado"):
            # /desde trae en cada mensaje su ultimo estado (Carlos, 21-11-2026),
            # tambien en los NUESTROS: se aplica a su notificacion aunque el
            # mensaje no se publique. Asi una recuperacion desatasca un envio
            # que se quedo en «en_cola» porque se perdio su acuse.
            self._alma_avanzar_envio(gateway, alma_id, mensaje["estado"])
        if alma_id and self._alma_ya_esta(gateway, canal, alma_id):
            return
        body = Markup()
        if mensaje.get("texto"):
            body = plaintext2html(mensaje["texto"])
        adjuntos = []
        for adjunto in mensaje.get("adjuntos") or []:
            # HOY EL PORTAL NO GUARDA LOS FICHEROS y la url llega vacia; Meta
            # los borra a los 7 dias. Se dice que existe y que no lo tenemos,
            # que es honesto, en vez de un enlace que dara 404.
            if not adjunto.get("url"):
                body += Markup("<p><i>%s</i></p>") % self.env._(
                    "Se recibió %(que)s, pero el fichero no está disponible.",
                    que=self._alma_que_es(adjunto),
                )
                continue
            descargado = self._alma_descargar(gateway, adjunto)
            if descargado:
                adjuntos.append(descargado)
            else:
                body += Markup("<p><i>%s</i></p>") % self.env._(
                    "No se pudo descargar %(que)s.",
                    que=self._alma_que_es(adjunto),
                )
        if not body and not adjuntos:
            return
        autor = self._alma_autor_del_mensaje(gateway, evento)
        # El controlador ya pone no_gateway_notification: lo que entra no se
        # vuelve a mandar. Al recuperar no pasa por el controlador, asi que se
        # pone aqui tambien.
        nuevo = (
            canal.sudo()
            .with_context(no_gateway_notification=True, alma_entrante=True)
            .message_post(
                body=body,
                author_id=autor.id or False,
                gateway_type="alma",
                date=evento["fecha_mensaje"],
                subtype_xmlid="mail.mt_comment",
                message_type="comment",
                attachments=adjuntos,
            )
        )
        nuevo.sudo().write(
            {
                "alma_mensaje_id": alma_id or False,
                # Un origen nuevo del portal no puede tumbar la entrada: una
                # Selection con un valor que no conoce da ValueError.
                "alma_origen": (
                    self._alma_origen(mensaje)
                    if self._alma_origen(mensaje) in dict(ORIGENES)
                    else False
                ),
            }
        )
        if (
            not canal.alma_ultimo_mensaje
            or evento["fecha_mensaje"] > canal.alma_ultimo_mensaje
        ):
            canal.sudo().alma_ultimo_mensaje = evento["fecha_mensaje"]
        self._post_process_message(nuevo, canal)

    def _alma_ya_esta(self, gateway, canal, alma_id):
        """¿Este mensaje del portal ya esta en Odoo? Dos casos:

        - Ya entro antes (el webhook y /desde se solapan a proposito).
        - ES EL ECO DE UNO QUE MANDO ODOO: el portal devuelve en `mensaje.id`
          el mismo `mensaje_id` del 202 de /enviar (Carlos, 21-11-2026), que
          se guarda en la notificacion. Ojo: el eco puede llegar ANTES de que
          ese id este guardado; lo que corta el bucle de verdad es que lo que
          entra no sale (`_send`). Esto solo evita el duplicado en pantalla.

        Los ACUSES no pasan por aqui: llevan el mismo id a proposito y tienen
        que llegar a su notificacion."""
        if (
            self.env["mail.message"]
            .sudo()
            .search_count(
                [
                    ("model", "=", "discuss.channel"),
                    ("res_id", "=", canal.id),
                    ("alma_mensaje_id", "=", alma_id),
                ],
                limit=1,
            )
        ):
            return True
        return bool(
            self.env["mail.notification"]
            .sudo()
            .search_count(
                [
                    ("gateway_message_id", "=", alma_id),
                    ("gateway_channel_id.gateway_id", "=", gateway.id),
                ],
                limit=1,
            )
        )

    def _alma_origen(self, mensaje):
        de = mensaje.get("de") or "contacto"
        # «cliente» era el nombre de la version 0.1 del contrato.
        return "contacto" if de == "cliente" else de

    def _alma_autor_del_mensaje(self, gateway, evento):
        de = self._alma_origen(evento["mensaje"])
        if de in ("ia", "sistema"):
            return self.env.ref("gts_chatroom.partner_alma_ia")
        if de in ("operador", "movil"):
            # Una persona del equipo, desde el ChatRoom del portal o desde su
            # propio WhatsApp: en Odoo sale como la empresa, que es quien
            # habla. Por donde contesto queda en `alma_origen`.
            return gateway.company_id.partner_id or self.env.company.partner_id
        if de != "contacto":
            _logger.warning("ChatRoom: origen desconocido %r, va como cliente", de)
        return self._get_author(gateway, evento) or self.env["res.partner"]

    def _alma_que_es(self, adjunto):
        # Sin la barra a proposito: /desde manda `mime: "image"` a secas, y el
        # webhook `image/jpeg`. `nombre` puede llegar null.
        mime = adjunto.get("mime") or ""
        nombre = adjunto.get("nombre")
        tipo = (
            self.env._("una imagen")
            if mime.startswith("image")
            else (
                self.env._("un audio")
                if mime.startswith("audio")
                else (
                    self.env._("un vídeo")
                    if mime.startswith("video")
                    else self.env._("un documento")
                )
            )
        )
        return f"{tipo} ({nombre})" if nombre else tipo

    def _get_author(self, gateway, update):
        """El contacto que escribe. BUSCAR ANTES DE CREAR: primero el enlace
        de la pasarela, luego el telefono; solo si no esta, se crea. Al reves
        se llena la agenda de duplicados en tres meses."""
        telefono = update.get("telefono")
        if not telefono:
            return False
        Enlace = self.env["res.partner.gateway.channel"].sudo()
        enlace = Enlace.search(
            [("gateway_id", "=", gateway.id), ("gateway_token", "=", telefono)],
            limit=1,
        )
        if enlace:
            return enlace.partner_id
        Partner = self.env["res.partner"].sudo()
        partner = Partner.search([("phone_sanitized", "=", f"+{telefono}")], limit=1)
        if not partner:
            partner = Partner.create(
                {
                    "name": update.get("nombre") or f"+{telefono}",
                    "phone": f"+{telefono}",
                }
            )
        Enlace.create(
            {
                "partner_id": partner.id,
                "gateway_id": gateway.id,
                "gateway_token": telefono,
            }
        )
        return partner.sudo(False)

    def _get_channel_vals(self, gateway, token, update):
        vals = super()._get_channel_vals(gateway, token, update)
        if update.get("nombre"):
            vals["name"] = update["nombre"]
        if update.get("conversacion_id"):
            vals["alma_conversacion_id"] = update["conversacion_id"]
        return vals

    def _alma_descargar(self, gateway, adjunto):
        url = adjunto.get("url")
        if not url:
            return False
        try:
            respuesta = requests.get(
                url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                timeout=TIMEOUT,
                stream=True,
            )
            respuesta.raise_for_status()
            contenido = respuesta.raw.read(MAX_ADJUNTO + 1, decode_content=True)
        except requests.RequestException as error:
            _logger.warning("ChatRoom: no se pudo descargar %s: %s", url, error)
            return False
        if len(contenido) > MAX_ADJUNTO:
            return False
        info = {}
        if (adjunto.get("mime") or "").startswith("audio"):
            info["voice"] = True
        return (adjunto.get("nombre") or "adjunto", contenido, info)

    def _alma_acuse(self, gateway, evento):
        acuse = evento["acuse"]
        mensaje_id = acuse.get("mensaje_id")
        if not mensaje_id:
            return
        # OJO: casar por el id QUE DEVOLVIO EL PORTAL al enviar, no por el de
        # Gupshup ni por el de Meta. Casar por el que no es no da ningun error:
        # ningun acuse encuentra su mensaje y todo se queda en «salio».
        notificaciones = self._alma_avanzar_envio(
            gateway,
            mensaje_id,
            acuse.get("estado"),
            acuse.get("alma_motivo"),
            acuse.get("detalle"),
            acuse.get("error"),
            pricing=acuse.get("pricing"),
        )
        if evento["estado"] and notificaciones:
            notificaciones.gateway_channel_id[:1]._alma_aplicar_estado(
                evento["estado"], evento["emitido_en"]
            )

    def _alma_avanzar_envio(
        self,
        gateway,
        mensaje_id,
        estado,
        motivo=False,
        detalle=False,
        frase=False,
        pricing=None,
    ):
        """Mueve el estado de lo que mando Odoo con ese id del portal (el del
        202). Lo usan el acuse y el `mensaje.estado` de /desde. Devuelve las
        notificaciones encontradas, se hayan movido o no."""
        notificaciones = (
            self.env["mail.notification"]
            .sudo()
            .search(
                [
                    ("gateway_message_id", "=", str(mensaje_id)),
                    ("gateway_channel_id.gateway_id", "=", gateway.id),
                ]
            )
        )
        cambiadas = notificaciones._alma_avanzar(estado, motivo, detalle, frase)
        for mensaje in cambiadas.mail_message_id:
            mensaje._notify_message_notification_update()
        # Los de un comunicado no tienen notificacion: casan por el mismo id.
        destinatarios = (
            self.env["gts.chatroom.comunicado.destinatario"]
            .sudo()
            .search(
                [
                    ("mensaje_id", "=", str(mensaje_id)),
                    ("comunicado_id.gateway_id", "=", gateway.id),
                ]
            )
        )
        destinatarios._alma_avanzar(estado, motivo, frase or detalle)
        if isinstance(pricing, dict) and pricing.get("categoria"):
            destinatarios.write(
                {
                    "cobrado_como": str(pricing["categoria"]).upper(),
                    "facturable": bool(pricing.get("facturable")),
                }
            )
        return notificaciones

    # ------------------------------------------------------------------
    # Salida
    # ------------------------------------------------------------------

    def _alma_peticion(self, gateway, metodo, ruta, **kwargs):
        if not gateway.alma_url:
            raise MailDeliveryException(
                self.env._("La pasarela %s no tiene la URL del portal.", gateway.name)
            )
        url = gateway.alma_url.rstrip("/") + ruta
        cabeceras = kwargs.pop("headers", {})
        cabeceras["Authorization"] = f"Bearer {gateway.token}"
        # SIN REINTENTOS AUTOMATICOS. Un POST no se reintenta solo: el portal
        # todavia no se protege de duplicados (Carlos, 21-11-2026) y un
        # timeout no dice si el mensaje salio; un envio fallido se queda en
        # «fallido» a la vista y lo reenvia una persona. Un GET tampoco: el
        # reintento de /desde es la siguiente pasada del cron, y un hilo de
        # cron no puede quedarse 2 x TIMEOUT esperando.
        #
        # SIN SEGUIR REDIRECCIONES. Medido el 21-11-2026: una ruta del portal
        # que no llega a su codigo contesta 307 a /login; requests la seguia
        # en el GET y la pagina de login, 200 en HTML, se leia como «ningun
        # evento» sin error. Una redireccion es siempre un fallo a la vista.
        # El lote de un comunicado valida cientos de contactos antes de
        # contestar: lo pide con su propio tiempo.
        timeout = kwargs.pop("timeout", TIMEOUT)
        return requests.request(
            metodo,
            url,
            headers=cabeceras,
            timeout=timeout,
            allow_redirects=False,
            **kwargs,
        )

    def _alma_ok(self, respuesta, datos):
        """Una respuesta del portal vale si es 2xx Y trae JSON. Un 2xx en HTML
        (una pagina de login, de error de Vercel…) no es el portal hablando:
        leerlo como «vacio» es el cero mudo que tapo el primer mensaje."""
        return 200 <= respuesta.status_code < 300 and bool(datos)

    def _send(
        self,
        gateway,
        record,
        auto_commit=False,
        raise_exception=False,
        parse_mode=False,
    ):
        """Prepara el envio y lo deja para DESPUES DEL COMMIT.

        EL BUCLE DEL 21-11-2026. El POST a /enviar iba dentro de la
        transaccion del mensaje. El portal mandaba su reenvio por el webhook
        ANTES de contestar el 202; esa otra peticion publicaba en el mismo
        canal y escribia su fila (`last_interest_dt`, el estado) y hacia
        commit primero. Al terminar, la transaccion del mensaje chocaba con
        ella (SerializationFailure) y Odoo, que reintenta solo los fallos de
        concurrencia (`odoo/service/model.py`, `retrying`, hasta 5 veces),
        DESHACIA el mensaje y volvia a ejecutar la peticion entera: otro
        mensaje, otra notificacion con otro id, otra `Idempotency-Key` y otro
        POST que el portal SI enviaba. Lo que se deshace en la base no
        deshace lo que ya salio por la red. (Deducido de la base de staging:
        el mensaje de David no esta, sus ecos si, y faltan cinco ids de
        mensaje y cinco de notificacion intercalados con los ecos.)

        Por eso la llamada va en `cr.postcommit`, como hace Odoo con sus
        propios webhooks (`ir_actions.py`, `_add_post_commit`): solo sale si
        el mensaje ya esta guardado para siempre, una vez, y un rollback la
        cancela. El resultado se escribe en un cursor nuevo."""
        mensaje = record.mail_message_id
        # RED DE SEGURIDAD: lo que vino del portal no vuelve a salir NUNCA.
        # Se publica con `no_gateway_notification` y no deberia llegar aqui;
        # si llega (otro camino, otro modulo), se corta y queda a la vista.
        # `alma_entrante` cubre el momento en que el mensaje acaba de crearse
        # y todavia no tiene escritos `alma_origen` ni `alma_mensaje_id`.
        if (
            self.env.context.get("alma_entrante")
            or mensaje.alma_origen
            or mensaje.alma_mensaje_id
        ):
            _logger.warning(
                "ChatRoom: el mensaje %s vino del portal; no se reenvia",
                mensaje.id,
            )
            record.sudo().write(
                {
                    "notification_status": "canceled",
                    "failure_reason": self.env._(
                        "Este mensaje vino del portal de Alma: no se vuelve a enviar."
                    ),
                }
            )
            return
        try:
            peticion = self._alma_preparar_envio(gateway, record)
        except MailDeliveryException as error:
            self._alma_marcar_fallido(record, str(error))
            if raise_exception:
                raise
            return
        if not peticion:
            return
        record.sudo().write({"notification_status": "process", "alma_estado": False})
        if modules.module.current_test:
            # En las pruebas no hay commit: se envia aqui, como `mail.mail`.
            self._alma_hacer_envio(gateway.id, record.id, peticion)
            return
        dbname = self.env.cr.dbname
        uid = self.env.uid
        contexto = dict(self.env.context)

        gateway_id = gateway.id
        record_id = record.id

        @self.env.cr.postcommit.add
        def _alma_enviar_tras_commit():
            self._alma_enviar_con_cursor_nuevo(
                dbname, uid, contexto, gateway_id, record_id, peticion
            )

    def _alma_preparar_envio(self, gateway, record):
        """Todo lo que se decide ANTES de enviar, dentro de la transaccion:
        si un error sale aqui, se ve y no ha salido nada."""
        if gateway.integrated_webhook_state != "integrated":
            # Desintegrar la pasarela es el freno de mano: corta las DOS
            # direcciones. El 21-11-2026 se desintegro para parar el bucle y
            # un mensaje escrito despues salio igual.
            raise MailDeliveryException(
                self.env._(
                    "La pasarela %s está desintegrada: no se envía nada hasta "
                    "que se vuelva a integrar.",
                    gateway.name,
                )
            )
        if record.mail_message_id.attachment_ids:
            # El contrato todavia no dice como viajan los ficheros. Mejor
            # un fallo visible que un mensaje que sale sin su adjunto.
            raise MailDeliveryException(
                self.env._("Enviar adjuntos todavia no esta disponible.")
            )
        if not gateway.alma_url:
            raise MailDeliveryException(
                self.env._("La pasarela %s no tiene la URL del portal.", gateway.name)
            )
        texto = html2plaintext(self._get_message_body(record) or "").strip()
        if not texto:
            return False
        canal = record.gateway_channel_id
        dbuuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid")
        return {
            # El id de la notificacion YA GUARDADA: tras el commit no hay
            # reintento que lo cambie, asi que la clave es estable.
            "idempotency_key": f"odoo-{dbuuid}-{record.id}",
            "json": {
                "numero": _digitos(gateway.alma_numero),
                "conversacion_id": canal.alma_conversacion_id or None,
                "a": canal.gateway_channel_token,
                "texto": texto,
                "operador": record.mail_message_id.author_id.name,
            },
        }

    def _alma_enviar_con_cursor_nuevo(
        self, dbname, uid, contexto, gateway_id, record_id, peticion
    ):
        """El POST UNA vez, fuera de cualquier transaccion. Lo que se
        reintenta, si choca con el webhook, es solo ESCRIBIR el resultado."""
        try:
            with Registry(dbname).cursor() as cr:
                env = api.Environment(cr, uid, contexto)
                gateway = env["mail.gateway"].sudo().browse(gateway_id)
                respuesta, error = env["mail.gateway.alma"]._alma_post_enviar(
                    gateway, peticion
                )
        except Exception as fallo:  # noqa: BLE001 - nunca puede tumbar el commit
            _logger.exception("ChatRoom: no se pudo enviar %s", record_id)
            respuesta, error = None, fallo
        for intento in range(1, MAX_ESCRITURAS + 1):
            try:
                with Registry(dbname).cursor() as cr:
                    env = api.Environment(cr, uid, contexto)
                    Alma = env["mail.gateway.alma"]
                    record = env["mail.notification"].sudo().browse(record_id)
                    Alma._alma_guardar_resultado(record, respuesta, error)
                return
            except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
                if intento == MAX_ESCRITURAS:
                    _logger.exception(
                        "ChatRoom: salio %s, pero no se pudo guardar el resultado",
                        record_id,
                    )
                    return
                time.sleep(0.5 * intento)

    def _alma_post_enviar(self, gateway, peticion):
        try:
            respuesta = self._alma_peticion(
                gateway,
                "POST",
                "/api/odoo/enviar",
                headers={"Idempotency-Key": peticion["idempotency_key"]},
                json=peticion["json"],
            )
        except Exception as error:  # noqa: BLE001
            return None, error
        return respuesta, None

    def _alma_hacer_envio(self, gateway_id, record_id, peticion):
        """Lo mismo, con el cursor de ahora (solo para las pruebas)."""
        gateway = self.env["mail.gateway"].sudo().browse(gateway_id)
        record = self.env["mail.notification"].sudo().browse(record_id)
        respuesta, error = self._alma_post_enviar(gateway, peticion)
        self._alma_guardar_resultado(record, respuesta, error)

    def _alma_guardar_resultado(self, record, respuesta, error):
        if error is not None or respuesta is None:
            _logger.warning("ChatRoom: no se pudo enviar %s: %s", record.id, error)
            self._alma_marcar_fallido(record, str(error))
            return
        datos = self._alma_json(respuesta)
        if not self._alma_ok(respuesta, datos):
            record.sudo().write(
                {
                    "alma_estado": "fallido",
                    "alma_motivo": datos.get("alma_motivo") or False,
                    "notification_status": "exception",
                    "failure_type": "unknown",
                    "failure_reason": self._alma_texto_error(respuesta, datos),
                }
            )
            record.mail_message_id._notify_message_notification_update()
            return
        # 202 {mensaje_id, estado} y, quiza, conversacion_id: puede no venir.
        # Con `repetido: true` el portal devuelve lo de la primera vez sin
        # volver a enviar; aqui se escribe lo mismo sobre lo mismo.
        record.sudo().write(
            {
                "gateway_message_id": datos.get("mensaje_id") or False,
                "failure_reason": False,
                "alma_estado": False,
            }
        )
        # OJO: NO se escribe `alma_mensaje_id` en el mensaje que sale. Ese
        # campo dice «vino del portal» y la red de seguridad de `_send` lo
        # lee. El eco se reconoce por `gateway_message_id` de la notificacion.
        record._alma_avanzar(datos.get("estado") or "en_cola")
        canal = record.gateway_channel_id
        if datos.get("conversacion_id") and not canal.alma_conversacion_id:
            canal.sudo().alma_conversacion_id = str(datos["conversacion_id"])
        record.mail_message_id._notify_message_notification_update()

    def _alma_marcar_fallido(self, record, texto):
        record.sudo().write(
            {
                "alma_estado": "fallido",
                "notification_status": "exception",
                "failure_type": "unknown",
                "failure_reason": texto,
            }
        )
        record.mail_message_id._notify_message_notification_update()

    def _alma_json(self, respuesta):
        try:
            datos = respuesta.json()
        except ValueError:
            return {}
        return datos if isinstance(datos, dict) else {}

    def _alma_texto_motivo(self, motivo, frase=None):
        """Nuestro texto si conocemos el motivo; si no, LA FRASE DEL PORTAL,
        que siempre manda una en castellano junto al motivo. Nunca el
        identificador a secas: «error_interno» no es para una persona
        (Carlos, 21-11-2026). Los motivos que el portal aun no manda (saldo,
        ventana de 24 h…) se quedan: son adelanto, no basura."""
        nuestro = MOTIVOS.get(motivo)
        if nuestro and frase and frase != nuestro:
            # Las dos: la nuestra dice que hacer en Odoo, la del portal trae
            # el detalle que solo el sabe (p. ej. de que cuenta es la clave).
            return f"{nuestro}\n\n{frase}"
        return nuestro or frase or motivo or self.env._("Error del portal.")

    def _alma_texto_error(self, respuesta, datos):
        """El texto para la persona de un error del portal, sea cual sea la
        llamada."""
        if respuesta.status_code == 401:
            # Llega SECO, sin cuerpo util (Carlos, 21-11-2026): clave
            # revocada, inexistente o ausente. Se reconoce por el codigo.
            return self.env._(
                "El portal de Alma no acepta la clave de esta pasarela. Revisa "
                "la clave en la configuración."
            )
        if 300 <= respuesta.status_code < 400:
            return self.env._(
                "El portal de Alma ha contestado con una redirección a "
                "%(destino)s en lugar de atender la llamada: esa ruta no está "
                "disponible en la dirección configurada. Revisa la URL del "
                "portal o avisa a quien lo mantiene.",
                destino=respuesta.headers.get("Location") or "?",
            )
        motivo = datos.get("alma_motivo")
        if motivo or datos.get("error"):
            return self._alma_texto_motivo(motivo, datos.get("error"))
        if not datos:
            return self.env._(
                "El portal de Alma ha contestado %(codigo)s, pero no con la "
                "respuesta esperada (no es JSON). Avisa a quien mantiene el "
                "portal.",
                codigo=respuesta.status_code,
            )
        return f"[{respuesta.status_code}] {respuesta.text[:300]}"

    def _alma_fecha(self, valor):
        return _fecha(valor)

    # ------------------------------------------------------------------
    # El relevo: Odoo -> portal
    # ------------------------------------------------------------------

    def _alma_cuerpo_relevo(self, gateway, canal, relevo, usuario):
        """EL CUERPO DE POST /api/odoo/relevo, en un solo sitio. Confirmado
        por Carlos el 21-11-2026: si cambia un nombre, se toca aqui."""
        return {
            "numero": _digitos(gateway.alma_numero),
            "conversacion_id": canal.alma_conversacion_id or None,
            "relevo": relevo,
            "asignado_a": canal._alma_correo(usuario) if relevo == "humano" else None,
            "operador": usuario.name,
        }

    def _alma_relevo(self, gateway, canal, relevo, usuario):
        """Pide el cambio de relevo al portal. Devuelve (datos, error).

        El portal devuelve SIEMPRE el estado resultante, tambien con un error
        (Carlos, 21-11-2026): pedir «humano» para alguien que no es miembro
        devuelve el estado sin asignar MAS el error. Ese estado es la verdad y
        se copia. Sin estado y con error, UserError y nada cambia."""
        cuerpo = self._alma_cuerpo_relevo(gateway, canal, relevo, usuario)
        dbuuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid")
        # Una clave por pulsacion. Dos pulsaciones son dos peticiones, pero el
        # cambio es el mismo: pedir dos veces «la cojo yo» no hace dano.
        clave = f"odoo-{dbuuid}-relevo-{canal.id}-{uuid.uuid4().hex}"
        try:
            respuesta = self._alma_peticion(
                gateway,
                "POST",
                "/api/odoo/relevo",
                headers={"Idempotency-Key": clave},
                json=cuerpo,
            )
        except (requests.RequestException, MailDeliveryException) as error:
            _logger.warning("ChatRoom: relevo de %s sin respuesta: %s", canal.id, error)
            raise UserError(
                self.env._(
                    "No se ha podido hablar con el portal de Alma, así que la "
                    "conversación sigue como estaba. Inténtelo de nuevo en un "
                    "momento.\n\nDetalle: %s",
                    error,
                )
            ) from error
        datos = self._alma_json(respuesta)
        if not 200 <= respuesta.status_code < 300:
            error = self._alma_texto_error_relevo(respuesta, datos, cuerpo)
            if not isinstance(datos.get("estado"), dict):
                raise UserError(error)
            return datos, error
        return datos, None

    def _alma_texto_error_relevo(self, respuesta, datos, cuerpo):
        """El error de un cambio de relevo, en castellano para quien pulsa.

        Lo que se sabe (Carlos, 21-11-2026): si se pide «humano» con un
        `asignado_a` que no es miembro de esa cuenta en el portal, el error
        lleva dentro el correo literal. Es lo que pasara el primer dia con
        todos los operadores, asi que tiene que leerse claro. El correo se
        busca en `detalle` y en `alma_motivo`; si no aparece, se ensena el
        error tal cual."""
        # Confirmado por Carlos (21-11-2026): 409 con alma_motivo
        # «operador_no_es_miembro», el correo SOLO en `detalle` y el estado.
        # El relevo SE HACE (humano, sin dueno): rechazarlo dejaria a la IA
        # contestando por encima de quien ya esta escribiendo.
        if datos.get("alma_motivo") == "operador_no_es_miembro":
            return self.env._(
                "%(correo)s no está dado de alta en el portal de Alma. La "
                "conversación ha pasado a atención de una persona, pero sin "
                "asignar a nadie. Pide que te den de alta en el portal.",
                correo=datos.get("detalle") or self.env._("Tu correo"),
            )
        if respuesta.status_code != 401 and cuerpo.get("relevo") == "humano":
            for campo in ("detalle", "alma_motivo"):
                texto = datos.get(campo)
                encontrado = isinstance(texto, str) and CORREO.search(texto)
                if encontrado:
                    return self.env._(
                        "%(correo)s no está dado de alta en el portal de Alma. "
                        "Pide que te den de alta y vuelve a intentarlo.",
                        correo=encontrado.group(0),
                    )
        texto = self._alma_texto_error(respuesta, datos)
        detalle = datos.get("detalle")
        if detalle and detalle not in texto:
            texto = f"{texto} {detalle}"
        return self.env._(
            "El portal de Alma no ha aceptado el cambio, así que la conversación "
            "sigue como estaba.\n\n%s",
            texto,
        )

    # ------------------------------------------------------------------
    # Recuperar
    # ------------------------------------------------------------------

    def _alma_recuperar(self, gateway):
        """Pide al portal los eventos desde CINCO MINUTOS ANTES del ultimo
        procesado. Los repetidos se descartan por `mensaje.id`; un hueco no se
        ve nunca.

        La respuesta (Carlos, 21-11-2026) es
        `{"eventos": [...], "truncado": bool, "hasta": "<ISO>"}`. Con
        `truncado` (tope de 500) se vuelve a pedir desde `hasta`, hasta que
        venga `false` o se acaben las vueltas.

        NO SE REORDENA. Dentro de una respuesta todos los eventos llevan el
        MISMO `emitido_en` (el reloj del portal al emitirla), asi que ordenar
        por el no ordena nada; el portal ya los manda por fecha del mensaje.
        `emitido_en` solo decide si un bloque de estado es mas nuevo que el
        guardado."""
        desde = gateway.alma_ultimo_evento or (datetime.now() - timedelta(days=1))
        desde -= timedelta(minutes=5)
        desde_iso = desde.replace(tzinfo=UTC).isoformat()
        procesados = 0
        for _vuelta in range(MAX_VUELTAS):
            respuesta = self._alma_peticion(
                gateway,
                "GET",
                "/api/odoo/desde",
                params={"numero": _digitos(gateway.alma_numero), "desde": desde_iso},
            )
            datos = self._alma_json(respuesta)
            if not self._alma_ok(respuesta, datos) or not isinstance(
                datos.get("eventos", []), list
            ):
                raise UserError(self._alma_texto_error(respuesta, datos))
            for evento in datos.get("eventos") or []:
                if isinstance(evento, dict) and self._alma_procesar(gateway, evento):
                    procesados += 1
            hasta = datos.get("hasta")
            if not datos.get("truncado") or not hasta or hasta == desde_iso:
                break
            desde_iso = hasta
        else:
            _logger.warning(
                "ChatRoom: la pasarela %s sigue truncada tras %s vueltas; el "
                "resto se pide en la siguiente recuperacion",
                gateway.id,
                MAX_VUELTAS,
            )
        return procesados

    @api.model
    def _alma_recuperar_todas(self):
        """Cron: /desde para cada pasarela alma integrada. El portal no
        reintenta el reenvio (Carlos, 21-11-2026): lo que no llego por el
        webhook se recoge aqui.

        UNA PASARELA ROTA NO PARA LAS DEMAS: su error se registra y se sigue.
        El savepoint deshace solo lo suyo; sin el, un error de base de datos
        dejaria la transaccion abortada para todas las que vienen detras."""
        pasarelas = (
            self.env["mail.gateway"]
            .sudo()
            .search(
                [
                    ("gateway_type", "=", "alma"),
                    ("integrated_webhook_state", "=", "integrated"),
                    ("alma_url", "!=", False),
                ]
            )
        )
        for gateway in pasarelas:
            try:
                with self.env.cr.savepoint():
                    alma = self.with_company(gateway.company_id or self.env.company)
                    procesados = alma._alma_recuperar(gateway)
                if procesados:
                    _logger.info(
                        "ChatRoom: %s eventos recuperados en la pasarela %s",
                        procesados,
                        gateway.id,
                    )
            except Exception:
                _logger.exception(
                    "ChatRoom: fallo al recuperar la pasarela %s", gateway.id
                )
