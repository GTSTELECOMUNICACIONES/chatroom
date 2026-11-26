"""EMPEZAR UNA CONVERSACIÓN DESDE CHATS (David, 26-11-2026): sin salir de la
pantalla, elegir el contacto, el canal y una plantilla, y mandarla.

Cualquier plantilla aprobada, no solo las de marketing: para escribir a
alguien que no ha escrito en 24 horas Meta exige una plantilla, sea la que
sea. Dentro de las 24 horas no hace falta: basta con abrir la conversación y
escribir.

DESDE CUALQUIER FICHA (26-11-2026): «Enviar WhatsApp» en el menú Acción de
los modelos a los que se aplica alguna plantilla abre esto mismo con la
ficha (`res_model`, `res_id`). El contacto sale de la ficha (o de su
`partner_id`), el número del «Campo del teléfono» de la plantilla y los
datos de sus campos. Queda una nota en el historial de la ficha.

La plantilla se publica en la conversación como un mensaje más del operador,
con `alma_plantilla_id` y `alma_parametros`: sale por el mismo camino que lo
que se escribe a mano (después del commit, con su acuse, su estado y su
error a la vista), solo que `_send` manda `plantilla` en vez de `texto`.
"""

import re
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import plaintext2html

from .gts_chatroom_comunicado import pista_y_ejemplo
from .gts_chatroom_plantilla_alta import solo_nombre_de_pila, valor_de_campo
from .mail_gateway_alma import _digitos

VENTANA = timedelta(hours=24)


class GtsChatroomNueva(models.TransientModel):
    _name = "gts.chatroom.nueva"
    _description = "Nueva conversación de WhatsApp"

    gateway_id = fields.Many2one(
        "mail.gateway",
        "Canal",
        required=True,
        domain=[
            ("gateway_type", "=", "alma"),
            ("integrated_webhook_state", "=", "integrated"),
        ],
        default=lambda self: self.env["res.partner"]._gts_pasarelas_alma()[:1],
    )
    # Sin readonly: un campo de solo lectura no viaja al guardar el diálogo.
    res_model = fields.Char()
    res_id = fields.Integer()
    registro_nombre = fields.Char("Desde", compute="_compute_registro_nombre")
    partner_id = fields.Many2one("res.partner", "Contacto")
    telefono = fields.Char(
        "Se le escribe al",
        compute="_compute_telefono",
        store=True,
        readonly=False,
        help="Con el prefijo del país. Sale de la ficha; se puede cambiar.",
    )
    canal_id = fields.Many2one(
        "discuss.channel", "Conversación", compute="_compute_conversacion"
    )
    ventana_abierta = fields.Boolean(compute="_compute_conversacion")
    plantilla_permitida_ids = fields.Many2many(
        "gts.chatroom.plantilla", compute="_compute_plantilla_permitida_ids"
    )
    plantilla_id = fields.Many2one(
        "gts.chatroom.plantilla",
        "Plantilla",
        domain="[('id', 'in', plantilla_permitida_ids)]",
        default=lambda self: (
            self.env["res.partner"]._gts_pasarelas_alma()[:1].plantilla_inicio_id
        ),
    )
    # ESCRIBIR COMO EN UN CHAT (David, 26-11-2026): «con estas variables es
    # incomodísimo». Si a la plantilla solo le falta UN dato por escribir
    # (los demás salen de la ficha, como el nombre), ese dato es el mensaje
    # y se escribe en un recuadro grande. Meta no admite saltos de línea en
    # un dato: se juntan en una línea al enviar.
    mensaje = fields.Text("Mensaje")
    posicion_mensaje = fields.Integer(compute="_compute_posicion_mensaje")
    categoria_texto = fields.Char(related="plantilla_id.categoria_texto")
    variables = fields.Integer(related="plantilla_id.variables_cuerpo")
    ejemplo_texto = fields.Text(related="plantilla_id.ejemplo_texto")
    dato_ids = fields.One2many("gts.chatroom.nueva.dato", "nueva_id", "Datos")
    vista_previa = fields.Text("Así llegará", compute="_compute_vista_previa")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        contexto = self.env.context
        if contexto.get("gts_desde_registro") and contexto.get("active_model"):
            res["res_model"] = contexto["active_model"]
            res["res_id"] = contexto.get("active_id") or 0
            registro = self._gts_registro_de(res["res_model"], res["res_id"])
            if registro._name == "res.partner":
                res["partner_id"] = registro.id
            elif "partner_id" in registro._fields and registro.partner_id:
                res["partner_id"] = registro.partner_id[:1].id
        return res

    @api.model
    def _gts_registro_de(self, modelo, res_id):
        if not modelo or modelo not in self.env or not res_id:
            return self.env["res.partner"]
        return self.env[modelo].browse(res_id).exists()

    def _gts_registro(self):
        """La ficha desde la que se manda; si se manda desde Chats, el
        contacto."""
        self.ensure_one()
        return self._gts_registro_de(self.res_model, self.res_id) or self.partner_id

    @api.depends("res_model", "res_id")
    def _compute_registro_nombre(self):
        for nueva in self:
            registro = nueva._gts_registro_de(nueva.res_model, nueva.res_id)
            nueva.registro_nombre = registro.display_name if registro else False

    @api.depends("gateway_id", "res_model")
    def _compute_plantilla_permitida_ids(self):
        Plantilla = self.env["gts.chatroom.plantilla"]
        for nueva in self:
            dominio = [
                ("gateway_id", "=", nueva.gateway_id.id),
                ("se_puede_enviar", "=", True),
            ]
            if nueva.res_model:
                dominio += [
                    "|",
                    ("model_id", "=", False),
                    ("model_id.model", "=", nueva.res_model),
                ]
            nueva.plantilla_permitida_ids = Plantilla.search(dominio)

    @api.depends("partner_id", "plantilla_id", "res_model", "res_id")
    def _compute_telefono(self):
        for nueva in self:
            registro = nueva._gts_registro()
            crudo = ""
            if registro and nueva.plantilla_id.campo_telefono and nueva.res_model:
                crudo = valor_de_campo(registro, nueva.plantilla_id.campo_telefono)
            if not crudo:
                crudo = nueva.partner_id.phone_sanitized or nueva.partner_id.phone
            formateado = crudo and (registro or nueva)._phone_format(number=crudo)
            nueva.telefono = formateado or crudo or False

    @api.depends("partner_id", "gateway_id", "telefono")
    def _compute_conversacion(self):
        Mensaje = self.env["mail.message"].sudo()
        for nueva in self:
            canal = nueva._gts_buscar_canal()
            nueva.canal_id = canal
            nueva.ventana_abierta = bool(
                canal
                and Mensaje.search_count(
                    [
                        ("model", "=", "discuss.channel"),
                        ("res_id", "=", canal.id),
                        ("alma_origen", "=", "contacto"),
                        ("date", ">=", fields.Datetime.now() - VENTANA),
                    ],
                    limit=1,
                )
            )

    def _gts_token(self):
        return _digitos(self.telefono) or _digitos(self.partner_id.phone_sanitized)

    def _gts_buscar_canal(self):
        """La conversación que ya hay con este contacto por este canal: por
        su enlace con la pasarela o por su teléfono."""
        if not self.gateway_id or not (self.partner_id or self.telefono):
            return self.env["discuss.channel"]
        tokens = set()
        if self.partner_id and not self.telefono:
            tokens = set(
                self.env["res.partner.gateway.channel"]
                .sudo()
                .search(
                    [
                        ("gateway_id", "=", self.gateway_id.id),
                        ("partner_id", "=", self.partner_id.id),
                    ]
                )
                .mapped("gateway_token")
            )
        if self._gts_token():
            tokens.add(self._gts_token())
        if not tokens:
            return self.env["discuss.channel"]
        return (
            self.env["discuss.channel"]
            .sudo()
            .search(
                [
                    ("gateway_id", "=", self.gateway_id.id),
                    ("gateway_channel_token", "in", list(tokens)),
                ],
                limit=1,
            )
        )

    @api.depends("plantilla_id")
    def _compute_posicion_mensaje(self):
        for nueva in self:
            plantilla = nueva.plantilla_id
            total = plantilla.variables_cuerpo or 0
            por_campo = {
                v.posicion for v in plantilla.variable_ids if v.origen == "campo"
            }
            libres = [n for n in range(1, total + 1) if n not in por_campo]
            nueva.posicion_mensaje = libres[0] if len(libres) == 1 else 0

    @staticmethod
    def _en_una_linea(texto):
        """Lo que Meta admite en un dato: sin saltos de línea, tabuladores ni
        más de cuatro espacios seguidos."""
        return re.sub(r"\s+", " ", texto or "").strip()

    @api.onchange("plantilla_id", "partner_id")
    def _onchange_plantilla_datos(self):
        """Una casilla por dato, ya rellena con el campo de la ficha o con
        el texto por defecto de la plantilla."""
        registro = self._gts_registro()
        por_posicion = {v.posicion: v for v in self.plantilla_id.variable_ids}
        comandos = [(5, 0, 0)]
        for n in range(1, (self.plantilla_id.variables_cuerpo or 0) + 1):
            variable = por_posicion.get(n)
            valor = ""
            if variable and variable.origen == "campo" and registro:
                valor = valor_de_campo(registro, variable.campo)
                if variable.formato == "nombre_pila":
                    valor = solo_nombre_de_pila(registro, variable.campo, valor)
            elif variable:
                valor = variable.por_defecto or ""
            comandos.append((0, 0, {"posicion": n, "valor": valor}))
        self.dato_ids = comandos

    def _lista_parametros(self):
        valores = [(d.valor or "").strip() for d in self.dato_ids.sorted("posicion")]
        n = self.posicion_mensaje
        if n and len(valores) >= n:
            valores[n - 1] = self._en_una_linea(self.mensaje)
        return valores

    @api.depends(
        "plantilla_id.cuerpo", "dato_ids.valor", "dato_ids.posicion", "mensaje"
    )
    def _compute_vista_previa(self):
        for nueva in self:
            texto = nueva.plantilla_id.cuerpo or ""
            for n, valor in enumerate(nueva._lista_parametros(), start=1):
                if valor:
                    texto = texto.replace(f"{{{{{n}}}}}", valor)
            nueva.vista_previa = texto or False

    # ------------------------------------------------------------------

    def _gts_preparar_canal(self):
        """La conversación con este contacto, creada si no la hay, con quien
        la abre dentro y fijada en su lateral."""
        self.ensure_one()
        if self.gateway_id.integrated_webhook_state != "integrated":
            raise UserError(
                self.env._("El canal %s no está integrado.", self.gateway_id.name)
            )
        canal = self._gts_buscar_canal()
        if not canal:
            token = self._gts_token()
            if not token:
                raise UserError(
                    self.env._(
                        "No hay un teléfono válido al que escribir. Ponlo con el "
                        "prefijo del país, por ejemplo +34 y el móvil."
                    )
                )
            # El enlace primero: así la conversación es de ESTE contacto y no
            # de otro con el mismo teléfono. Sin contacto, la pasarela lo
            # busca por el teléfono o lo crea (`_get_author`).
            Enlace = self.env["res.partner.gateway.channel"].sudo()
            if self.partner_id and not Enlace.search_count(
                [
                    ("gateway_id", "=", self.gateway_id.id),
                    ("gateway_token", "=", token),
                ],
                limit=1,
            ):
                Enlace.create(
                    {
                        "partner_id": self.partner_id.id,
                        "gateway_id": self.gateway_id.id,
                        "gateway_token": token,
                    }
                )
            nombre = self.partner_id.name or self.registro_nombre or f"+{token}"
            canal = self.env["mail.gateway.alma"]._get_channel(
                self.gateway_id.sudo(),
                token,
                {"telefono": token, "nombre": nombre},
                force_create=True,
            )
        canal = canal.sudo()
        canal._add_members(users=self.env.user, post_joined_message=False)
        canal.with_user(self.env.user).channel_pin(pinned=True)
        return canal

    def _gts_cerrar_y_abrir(self, canal):
        # El lateral de Chats abre la conversación al cerrarse el diálogo.
        return {
            "type": "ir.actions.act_window_close",
            "infos": {"gts_canal_id": canal.id},
        }

    def accion_abrir(self):
        """Solo abrir la conversación, para escribir dentro de las 24 h."""
        self.ensure_one()
        canal = self._gts_preparar_canal()
        if self.res_model:
            # Desde una ficha: se va a la conversación, en Chats.
            return canal.action_alma_abrir()
        return self._gts_cerrar_y_abrir(canal)

    def accion_enviar(self):
        self.ensure_one()
        plantilla = self.plantilla_id
        if not plantilla:
            raise UserError(self.env._("Elige la plantilla que quieres enviar."))
        if not plantilla.se_puede_enviar:
            raise UserError(
                plantilla.por_que_no or self.env._("Esta plantilla no se puede enviar.")
            )
        valores = self._lista_parametros()
        if self.posicion_mensaje and not self._en_una_linea(self.mensaje):
            raise UserError(self.env._("Escribe el mensaje."))
        if len(valores) != (plantilla.variables_cuerpo or 0) or not all(valores):
            raise UserError(
                self.env._(
                    "La plantilla %(p)s necesita %(n)s datos y falta alguno por "
                    "rellenar.",
                    p=plantilla.name,
                    n=plantilla.variables_cuerpo,
                )
            )
        for valor in valores:
            if "\n" in valor or "\t" in valor or "     " in valor:
                raise UserError(
                    self.env._(
                        "El dato «%s» lleva saltos de línea, tabuladores o más de "
                        "cuatro espacios seguidos: WhatsApp no lo acepta.",
                        valor,
                    )
                )
        canal = self._gts_preparar_canal()
        cuerpo = plaintext2html(self.vista_previa or "") + Markup(
            "<p><i>%s</i></p>"
        ) % self.env._("Plantilla «%s»", plantilla.name)
        canal.with_user(self.env.user).message_post(
            body=cuerpo,
            author_id=self.env.user.partner_id.id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            alma_plantilla_id=plantilla.id,
            alma_parametros=valores,
        )
        registro = self._gts_registro_de(self.res_model, self.res_id)
        if not registro:
            return self._gts_cerrar_y_abrir(canal)
        if hasattr(registro, "message_post"):
            # Que en la ficha quede que se le escribió, y con qué.
            registro.message_post(
                body=Markup("<p>%s</p>")
                % self.env._(
                    "WhatsApp enviado al %(tel)s con la plantilla «%(p)s».",
                    tel=self.telefono or "",
                    p=plantilla.name,
                )
                + plaintext2html(self.vista_previa or ""),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._(
                    "Enviado. La conversación está en el ChatRoom, en Chats."
                ),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class GtsChatroomNuevaDato(models.TransientModel):
    _name = "gts.chatroom.nueva.dato"
    _description = "Dato de la plantilla de una conversación nueva"
    _order = "posicion"

    nueva_id = fields.Many2one("gts.chatroom.nueva", required=True, ondelete="cascade")
    posicion = fields.Integer("Dato", required=True)
    pista = fields.Char("Dónde va", compute="_compute_pista")
    ejemplo = fields.Char("Ejemplo", compute="_compute_pista")
    valor = fields.Char("Texto")

    @api.depends("posicion", "nueva_id.plantilla_id.cuerpo")
    def _compute_pista(self):
        for dato in self:
            dato.pista, dato.ejemplo = pista_y_ejemplo(
                self.env, dato.nueva_id.plantilla_id, dato.posicion
            )
