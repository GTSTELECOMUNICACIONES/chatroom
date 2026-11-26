"""EMPEZAR UNA CONVERSACIÓN DESDE CHATS (David, 26-11-2026): sin salir de la
pantalla, elegir el contacto, el canal y una plantilla, y mandarla.

Cualquier plantilla aprobada, no solo las de marketing: para escribir a
alguien que no ha escrito en 24 horas Meta exige una plantilla, sea la que
sea. Dentro de las 24 horas no hace falta: basta con abrir la conversación y
escribir.

La plantilla se publica en la conversación como un mensaje más del operador,
con `alma_plantilla_id` y `alma_parametros`: sale por el mismo camino que lo
que se escribe a mano (después del commit, con su acuse, su estado y su
error a la vista), solo que `_send` manda `plantilla` en vez de `texto`.
"""

from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import plaintext2html

from .gts_chatroom_comunicado import pista_y_ejemplo
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
    partner_id = fields.Many2one("res.partner", "Contacto", required=True)
    telefono = fields.Char("Se le escribe al", compute="_compute_conversacion")
    canal_id = fields.Many2one(
        "discuss.channel", "Conversación", compute="_compute_conversacion"
    )
    ventana_abierta = fields.Boolean(compute="_compute_conversacion")
    plantilla_id = fields.Many2one(
        "gts.chatroom.plantilla",
        "Plantilla",
        domain="[('gateway_id', '=', gateway_id), ('se_puede_enviar', '=', True)]",
    )
    categoria_texto = fields.Char(related="plantilla_id.categoria_texto")
    variables = fields.Integer(related="plantilla_id.variables_cuerpo")
    ejemplo_texto = fields.Text(related="plantilla_id.ejemplo_texto")
    dato_ids = fields.One2many("gts.chatroom.nueva.dato", "nueva_id", "Datos")
    vista_previa = fields.Text("Así llegará", compute="_compute_vista_previa")

    @api.depends("partner_id", "gateway_id")
    def _compute_conversacion(self):
        Mensaje = self.env["mail.message"].sudo()
        for nueva in self:
            telefono = _digitos(nueva.partner_id.phone_sanitized)
            nueva.telefono = f"+{telefono}" if telefono else False
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
        return _digitos(self.partner_id.phone_sanitized)

    def _gts_buscar_canal(self):
        """La conversación que ya hay con este contacto por este canal: por
        su enlace con la pasarela o por su teléfono."""
        if not self.gateway_id or not self.partner_id:
            return self.env["discuss.channel"]
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

    @api.onchange("plantilla_id")
    def _onchange_plantilla_datos(self):
        self.dato_ids = [(5, 0, 0)] + [
            (0, 0, {"posicion": n})
            for n in range(1, (self.plantilla_id.variables_cuerpo or 0) + 1)
        ]

    def _lista_parametros(self):
        return [(d.valor or "").strip() for d in self.dato_ids.sorted("posicion")]

    @api.depends("plantilla_id.cuerpo", "dato_ids.valor", "dato_ids.posicion")
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
                        "%s no tiene un teléfono válido. Ponle el móvil con el "
                        "prefijo del país en su ficha.",
                        self.partner_id.display_name,
                    )
                )
            # El enlace primero: así la conversación es de ESTE contacto y no
            # de otro con el mismo teléfono.
            self.partner_id.accion_enlazar_whatsapp()
            canal = self.env["mail.gateway.alma"]._get_channel(
                self.gateway_id.sudo(),
                token,
                {"telefono": token, "nombre": self.partner_id.name},
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
        return self._gts_cerrar_y_abrir(self._gts_preparar_canal())

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
        return self._gts_cerrar_y_abrir(canal)


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
