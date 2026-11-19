"""El transporte entre Odoo y el portal de Alma.

EL CONTRATO, en tres flujos y ninguno en el camino critico del otro:

    portal -> Odoo   cada evento (mensaje, acuse), CON el estado del relevo
    Odoo -> portal   POST /api/odoo/enviar, lo que escribe una persona aqui
    Odoo -> portal   GET  /api/odoo/desde, para recuperar lo que se perdio

La forma exacta de cada evento esta en `_alma_leer_evento`, y SOLO ahi: si el
portal cambia un nombre, se toca una funcion y no todo el fichero.
"""

import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime, timedelta

import requests
from markupsafe import Markup

from odoo import models
from odoo.http import request
from odoo.tools import html2plaintext, plaintext2html

from odoo.addons.base.models.ir_mail_server import MailDeliveryException

from .mail_message import ORIGENES

_logger = logging.getLogger(__name__)

# Margen para la hora de la firma. El mismo que se acordo para la pantalla.
MARGEN_FIRMA = 300
TIMEOUT = 10
# Un adjunto mas grande no se descarga: se deja una linea diciendolo. Un
# fichero de 200 MB metido en la transaccion del webhook la tumba.
MAX_ADJUNTO = 25 * 1024 * 1024

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
}


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
                   adjuntos: [{nombre, mime, url o null}]},
         acuse: {mensaje_id, estado, alma_motivo, detalle},
         estado: {relevo: ia|humano|resuelta, asignado_a, espera,
                  motivo_relevo}}
        """
        conversacion = update.get("conversacion") or {}
        contacto = conversacion.get("contacto") or {}
        return {
            "tipo": update.get("tipo"),
            "emitido_en": _fecha(update.get("emitido_en")),
            "numero": _digitos(update.get("numero")),
            "conversacion_id": conversacion.get("id") and str(conversacion["id"]),
            "telefono": _digitos(contacto.get("telefono")),
            "nombre": contacto.get("nombre"),
            "mensaje": update.get("mensaje") or {},
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
        if alma_id and self.env["mail.message"].sudo().search_count(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", canal.id),
                ("alma_mensaje_id", "=", alma_id),
            ],
            limit=1,
        ):
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
            .with_context(no_gateway_notification=True)
            .message_post(
                body=body,
                author_id=autor.id or False,
                gateway_type="alma",
                date=evento["emitido_en"],
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
        canal.sudo().alma_ultimo_mensaje = evento["emitido_en"]
        self._post_process_message(nuevo, canal)

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
        mime = adjunto.get("mime") or ""
        nombre = adjunto.get("nombre")
        tipo = (
            self.env._("una imagen")
            if mime.startswith("image/")
            else (
                self.env._("un audio")
                if mime.startswith("audio/")
                else (
                    self.env._("un vídeo")
                    if mime.startswith("video/")
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
        if (adjunto.get("mime") or "").startswith("audio/"):
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
        cambiadas = notificaciones._alma_avanzar(
            acuse.get("estado"), acuse.get("alma_motivo"), acuse.get("detalle")
        )
        for mensaje in cambiadas.mail_message_id:
            mensaje._notify_message_notification_update()
        if evento["estado"] and notificaciones:
            notificaciones.gateway_channel_id[:1]._alma_aplicar_estado(
                evento["estado"], evento["emitido_en"]
            )

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
        # UN SOLO REINTENTO, y solo si no hubo respuesta. Odoo no reintenta
        # fallos de red, y aqui se puede porque la clave de idempotencia hace
        # que el portal no lo mande dos veces.
        for intento in range(2):
            try:
                return requests.request(
                    metodo, url, headers=cabeceras, timeout=TIMEOUT, **kwargs
                )
            except (requests.ConnectionError, requests.Timeout):
                if intento:
                    raise

    def _send(
        self,
        gateway,
        record,
        auto_commit=False,
        raise_exception=False,
        parse_mode=False,
    ):
        canal = record.gateway_channel_id
        try:
            if record.mail_message_id.attachment_ids:
                # El contrato todavia no dice como viajan los ficheros. Mejor
                # un fallo visible que un mensaje que sale sin su adjunto.
                raise MailDeliveryException(
                    self.env._("Enviar adjuntos todavia no esta disponible.")
                )
            texto = html2plaintext(self._get_message_body(record) or "").strip()
            if not texto:
                return
            dbuuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid")
            respuesta = self._alma_peticion(
                gateway,
                "POST",
                "/api/odoo/enviar",
                headers={"Idempotency-Key": f"odoo-{dbuuid}-{record.id}"},
                json={
                    "numero": _digitos(gateway.alma_numero),
                    "conversacion_id": canal.alma_conversacion_id or None,
                    "a": canal.gateway_channel_token,
                    "texto": texto,
                    "operador": record.mail_message_id.author_id.name,
                },
            )
            datos = self._alma_json(respuesta)
            if respuesta.status_code >= 400:
                motivo = datos.get("alma_motivo")
                record.sudo().write(
                    {
                        "alma_estado": "fallido",
                        "alma_motivo": motivo or False,
                        "notification_status": "exception",
                        "failure_type": "unknown",
                        "failure_reason": (
                            self._alma_texto_motivo(motivo)
                            if motivo
                            else f"[{respuesta.status_code}] {respuesta.text[:300]}"
                        ),
                    }
                )
            else:
                vals = {
                    "gateway_message_id": datos.get("mensaje_id") or False,
                    "failure_reason": False,
                    "alma_estado": False,
                }
                record.sudo().write(vals)
                # El mismo id en el mensaje: si el portal lo devuelve como
                # evento, se descarta como repetido en vez de salir dos veces.
                record.mail_message_id.sudo().alma_mensaje_id = (
                    datos.get("mensaje_id") or False
                )
                record._alma_avanzar(datos.get("estado") or "en_cola")
                if datos.get("conversacion_id") and not canal.alma_conversacion_id:
                    canal.sudo().alma_conversacion_id = str(datos["conversacion_id"])
        except Exception as error:
            _logger.warning("ChatRoom: no se pudo enviar %s: %s", record.id, error)
            if raise_exception:
                raise MailDeliveryException(
                    self.env._("No se pudo enviar el mensaje por WhatsApp.")
                ) from error
            record.sudo().write(
                {
                    "alma_estado": "fallido",
                    "notification_status": "exception",
                    "failure_type": "unknown",
                    "failure_reason": str(error),
                }
            )
        if auto_commit is True:
            # pylint: disable=invalid-commit
            self.env.cr.commit()

    def _alma_json(self, respuesta):
        try:
            datos = respuesta.json()
        except ValueError:
            return {}
        return datos if isinstance(datos, dict) else {}

    def _alma_texto_motivo(self, motivo):
        return MOTIVOS.get(motivo) or motivo or self.env._("Error del portal.")

    # ------------------------------------------------------------------
    # Recuperar
    # ------------------------------------------------------------------

    def _alma_recuperar(self, gateway):
        """Pide al portal los eventos desde CINCO MINUTOS ANTES del ultimo
        procesado. Los repetidos se descartan; un hueco no se ve nunca."""
        desde = gateway.alma_ultimo_evento or (datetime.now() - timedelta(days=1))
        desde -= timedelta(minutes=5)
        respuesta = self._alma_peticion(
            gateway,
            "GET",
            "/api/odoo/desde",
            params={
                "numero": _digitos(gateway.alma_numero),
                "desde": desde.replace(tzinfo=UTC).isoformat(),
            },
        )
        respuesta.raise_for_status()
        eventos = self._alma_json(respuesta).get("eventos") or []
        # Ordenar por emitido_en, nunca por llegada.
        eventos.sort(key=lambda e: _fecha(e.get("emitido_en")))
        procesados = 0
        for evento in eventos:
            if self._alma_procesar(gateway, evento):
                procesados += 1
        return procesados
