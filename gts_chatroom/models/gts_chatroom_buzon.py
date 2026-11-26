"""LOS BUZONES (David, 26-11-2026): como en Superchat, «una carpeta de
canales» con su icono, sus canales y quién los atiende. Son como dos equipos
de atención al cliente: un operador solo ve las conversaciones de sus
buzones, también al buscar.

Viven SOLO en Odoo. El ChatRoom de Odoo es otro producto que el portal: lo
que se configura aquí se queda aquí (David, 26-11-2026).

Cómo se aplica:
- Cada canal (`mail.gateway`) está en un solo buzón. «Central» existe
  siempre, no se borra y recoge los canales que no estén en otro.
- Los operadores del buzón SON los miembros de sus canales
  (`mail.gateway.member_ids`): la pasarela de la OCA mete a esos miembros en
  cada conversación nueva, y el portal recibe esa lista para `asignado_a`.
  Aquí se escribe esa lista; a mano ya no se toca.
- Una regla de registro global sobre `discuss.channel` esconde las
  conversaciones de los buzones en los que no se está
  (`data/buzones_permisos.xml`).
- Al quitar a alguien de un buzón deja de ser miembro de sus
  conversaciones; al añadirlo, entra en las que no están resueltas.
"""

import unicodedata

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


def es_un_emoji(texto):
    """Un solo dibujo: sin letras, cifras ni espacios, y corto (un emoji con
    tono de piel o banderas son varios puntos de código)."""
    texto = (texto or "").strip()
    if not texto or len(texto) > 8:
        return False
    return not any(unicodedata.category(c)[0] in ("L", "N", "Z") for c in texto)


class GtsChatroomBuzon(models.Model):
    _name = "gts.chatroom.buzon"
    _description = "Buzón del ChatRoom"
    _order = "es_central desc, sequence, id"

    name = fields.Char("Nombre", required=True)
    icono = fields.Char(
        "Icono",
        default="📁",
        required=True,
        help="Un solo emoji: el dibujo que sale junto al nombre en Chats.",
    )
    sequence = fields.Integer("Orden", default=10)
    es_central = fields.Boolean("Central", readonly=True, copy=False)
    canal_ids = fields.One2many("mail.gateway", "gts_buzon_id", "Canales")
    usuario_ids = fields.Many2many(
        "res.users",
        "gts_chatroom_buzon_res_users_rel",
        "buzon_id",
        "user_id",
        "Operadores",
        domain=[("share", "=", False)],
        help="Quién atiende este buzón. Solo ellos ven sus conversaciones.",
    )
    color = fields.Integer("Color")

    @api.constrains("icono")
    def _comprobar_icono(self):
        for buzon in self:
            if not es_un_emoji(buzon.icono):
                raise ValidationError(
                    self.env._(
                        "El icono tiene que ser un solo emoji, por ejemplo 📮 o 🏁."
                    )
                )

    @api.constrains("es_central")
    def _comprobar_central(self):
        if self.search_count([("es_central", "=", True)]) > 1:
            raise ValidationError(self.env._("Solo puede haber un buzón Central."))

    @api.depends("name", "icono")
    def _compute_display_name(self):
        for buzon in self:
            buzon.display_name = f"{buzon.icono or ''} {buzon.name or ''}".strip()

    # ------------------------------------------------------------------
    # Crear, cambiar y borrar
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        buzones = super().create(vals_list)
        buzones.canal_ids._gts_aplicar_buzon()
        return buzones

    def write(self, vals):
        antes = self.canal_ids
        res = super().write(vals)
        if {"usuario_ids", "canal_ids"} & set(vals):
            # Los que salen de un buzón por `canal_ids` vuelven a Central.
            sueltos = (antes - self.canal_ids).filtered(lambda g: not g.gts_buzon_id)
            if sueltos:
                sueltos.write({"gts_buzon_id": self._gts_central().id})
            (antes | self.canal_ids)._gts_aplicar_buzon()
        return res

    def unlink(self):
        if self.filtered("es_central"):
            raise UserError(
                self.env._(
                    "El buzón Central no se borra: es donde caen los canales "
                    "nuevos. Si no lo usas, déjalo sin canales."
                )
            )
        canales = self.canal_ids
        central = self._gts_central()
        canales.write({"gts_buzon_id": central.id})
        return super().unlink()

    @api.model
    def _gts_central(self):
        central = (
            self.sudo()
            .with_context(active_test=False)
            .search([("es_central", "=", True)], limit=1)
        )
        if not central:
            central = self.sudo().create(
                {
                    "name": self.env._("Central"),
                    "icono": "📮",
                    "es_central": True,
                    "sequence": 0,
                }
            )
        return central

    @api.model
    def _gts_preparar_central(self):
        """Al instalar y en cada actualización: que exista Central y que
        ningún canal se quede sin buzón. La PRIMERA vez Central se lleva a
        todos los que ya atendían algún canal, para que nadie deje de ver lo
        que veía (si no, la regla de los buzones lo escondería todo)."""
        central = self._gts_central()
        Gateway = self.env["mail.gateway"].sudo().with_context(active_test=False)
        sueltos = Gateway.search([("gts_buzon_id", "=", False)])
        Parametros = self.env["ir.config_parameter"].sudo()
        if not Parametros.get_param("gts_chatroom.central_preparado"):
            # Una sola vez: si luego alguien vacía Central a propósito, una
            # actualización no se lo vuelve a llenar.
            grupo = self.env.ref("mail_gateway.gateway_user", raise_if_not_found=False)
            conversaciones = (
                self.env["discuss.channel"]
                .sudo()
                .search([("channel_type", "=", "gateway")])
            )
            usuarios = (
                Gateway.search([]).member_ids
                | conversaciones.channel_member_ids.partner_id.user_ids
                | (grupo.user_ids if grupo else self.env["res.users"])
            ).filtered(lambda u: not u.share and u.active)
            central.usuario_ids = [(4, u.id) for u in usuarios]
            Parametros.set_param("gts_chatroom.central_preparado", "1")
        if sueltos:
            # Sin `_gts_aplicar_buzon`: la primera vez cada canal se queda
            # con los miembros que tenía; Central los incluye a todos.
            sueltos.with_context(gts_sin_aplicar_buzon=True).write(
                {"gts_buzon_id": central.id}
            )
        return True

    # ------------------------------------------------------------------
    # Para el lateral de Chats
    # ------------------------------------------------------------------

    @api.model
    def gts_mis_buzones(self):
        """Los buzones del usuario, con sus canales y cuántas esperan."""
        if not self.env.user.has_group("mail_gateway.gateway_user"):
            return []
        buzones = self.sudo().search([("usuario_ids", "in", self.env.uid)])
        esperando = self.env["mail.gateway"]._gts_esperando_por_canal()
        return [
            {
                "id": buzon.id,
                "nombre": buzon.name,
                "icono": buzon.icono,
                "central": buzon.es_central,
                "canales": [
                    {
                        "id": canal.id,
                        "nombre": canal.name or canal.canal_etiqueta,
                        "canal": canal.canal_etiqueta,
                        "numero": canal.canal_numero or "",
                    }
                    for canal in buzon.canal_ids.filtered(
                        lambda g: g.gateway_type == "alma"
                    )
                ],
                "esperando": sum(esperando.get(c.id, 0) for c in buzon.canal_ids),
            }
            for buzon in buzones
        ]


class MailGateway(models.Model):
    _inherit = "mail.gateway"

    gts_buzon_id = fields.Many2one(
        "gts.chatroom.buzon",
        "Buzón",
        ondelete="restrict",
        index=True,
        default=lambda self: self.env["gts.chatroom.buzon"]._gts_central(),
        help="Quién atiende este canal lo decide su buzón.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        canales = super().create(vals_list)
        canales._gts_aplicar_buzon()
        return canales

    def write(self, vals):
        res = super().write(vals)
        if "gts_buzon_id" in vals and not self.env.context.get("gts_sin_aplicar_buzon"):
            self._gts_aplicar_buzon()
        return res

    def _gts_aplicar_buzon(self):
        """Los operadores del buzón pasan a ser los del canal y los de sus
        conversaciones. Quien ya no está, sale de todas; quien entra, se une
        a las que no están resueltas (las resueltas vuelven a todos al
        reabrirse)."""
        if self.env.context.get("gts_sin_aplicar_buzon"):
            return
        Canal = self.env["discuss.channel"].sudo()
        for gateway in self.sudo():
            buzon = (
                gateway.gts_buzon_id or self.env["gts.chatroom.buzon"]._gts_central()
            )
            usuarios = buzon.usuario_ids.filtered("active")
            if gateway.member_ids != usuarios:
                gateway.member_ids = [(6, 0, usuarios.ids)]
            conversaciones = Canal.search([("gateway_id", "=", gateway.id)])
            if not conversaciones:
                continue
            # Solo operadores (usuarios internos): el cliente, como contacto o
            # como invitado, no se toca.
            fuera = conversaciones.channel_member_ids.filtered(
                lambda m, u=usuarios: m.partner_id
                and m.partner_id not in u.partner_id
                and any(not x.share for x in m.partner_id.user_ids)
            )
            fuera.unlink()
            for conversacion in conversaciones.filtered(
                lambda c: c.alma_cola != "resuelta"
            ):
                dentro = conversacion.channel_member_ids.partner_id
                faltan = usuarios.filtered(lambda u, d=dentro: u.partner_id not in d)
                if faltan:
                    conversacion._add_members(users=faltan, post_joined_message=False)
