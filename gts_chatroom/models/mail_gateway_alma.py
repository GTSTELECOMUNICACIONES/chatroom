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

from odoo import api, models
from odoo.http import request
from odoo.tools import html2plaintext, plaintext2html

from odoo.exceptions import UserError

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
            acuse.get("estado"),
            acuse.get("alma_motivo"),
            acuse.get("detalle"),
            acuse.get("error"),
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
        return requests.request(
            metodo,
            url,
            headers=cabeceras,
            timeout=TIMEOUT,
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
            if not self._alma_ok(respuesta, datos):
                motivo = datos.get("alma_motivo")
                record.sudo().write(
                    {
                        "alma_estado": "fallido",
                        "alma_motivo": motivo or False,
                        "notification_status": "exception",
                        "failure_type": "unknown",
                        "failure_reason": self._alma_texto_error(respuesta, datos),
                    }
                )
            else:
                # 202 {mensaje_id, estado} y, quiza, conversacion_id: puede no
                # venir. Con `repetido: true` el portal devuelve lo de la
                # primera vez sin volver a enviar; aqui se escribe lo mismo
                # sobre lo mismo, asi que no se duplica nada.
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

    def _alma_texto_motivo(self, motivo, frase=None):
        """Nuestro texto si conocemos el motivo; si no, LA FRASE DEL PORTAL,
        que siempre manda una en castellano junto al motivo. Nunca el
        identificador a secas: «error_interno» no es para una persona
        (Carlos, 21-11-2026). Los motivos que el portal aun no manda (saldo,
        ventana de 24 h…) se quedan: son adelanto, no basura."""
        return MOTIVOS.get(motivo) or frase or motivo or self.env._("Error del portal.")

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
