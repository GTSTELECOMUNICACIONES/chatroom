"""LISTAS DE DIFUSION (David, 25-11-2026): «los de N2», «los de N3»…

Una lista es un grupo de contactos de Odoo. No se usan las de Email
Marketing: su contacto no guarda el movil sin `mass_mailing_sms`, y en la 19
el contacto de Odoo ya lo tiene (`phone`, con `phone_sanitized`).

Las bajas NO se guardan aqui. Las mira el portal en cada envio y segun la
categoria de la plantilla (Carlos): la misma persona puede no recibir un
comunicado de marketing y si uno de servicio. Una baja copiada en Odoo
envejeceria.

Otro modulo puede llenar una lista desde sus fichas sin depender de este:
basta con que su modelo tenga `_gts_chatroom_contactos()` y abra el asistente
`action_lista_anadir` con `active_model` y `active_ids` (lo hace la escuela
desde las matriculas).
"""

from odoo import api, fields, models
from odoo.exceptions import UserError


class GtsChatroomLista(models.Model):
    _name = "gts.chatroom.lista"
    _description = "Lista de difusión de WhatsApp"
    _order = "name"

    name = fields.Char("Nombre", required=True)
    partner_ids = fields.Many2many(
        "res.partner",
        "gts_chatroom_lista_partner_rel",
        "lista_id",
        "partner_id",
        string="Contactos",
    )
    contactos = fields.Integer("Contactos", compute="_compute_contactos")
    sin_telefono = fields.Integer(
        "Sin teléfono válido",
        compute="_compute_contactos",
        help="Contactos sin un teléfono que se pueda usar en WhatsApp: no les "
        "llegará ningún comunicado.",
    )
    nota = fields.Text("Para qué es")
    active = fields.Boolean(default=True)

    @api.depends("partner_ids", "partner_ids.phone_sanitized")
    def _compute_contactos(self):
        for lista in self:
            lista.contactos = len(lista.partner_ids)
            lista.sin_telefono = len(
                lista.partner_ids.filtered(lambda p: not p.phone_sanitized)
            )

    def accion_ver_sin_telefono(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Sin teléfono válido · %s", self.name),
            "res_model": "res.partner",
            "view_mode": "list,form",
            "domain": [
                ("id", "in", self.partner_ids.ids),
                ("phone_sanitized", "=", False),
            ],
        }


class GtsChatroomListaAnadir(models.TransientModel):
    _name = "gts.chatroom.lista.anadir"
    _description = "Añadir contactos a una lista de difusión"

    lista_id = fields.Many2one(
        "gts.chatroom.lista",
        "Lista",
        help="Vacía, se crea una nueva con el nombre de abajo.",
    )
    nombre_nueva = fields.Char("Nombre de la lista nueva")
    partner_ids = fields.Many2many("res.partner", string="Contactos")
    vaciar = fields.Boolean(
        "Vaciar la lista antes",
        help="Marcado, la lista queda solo con estos contactos: es lo que se "
        "quiere al rehacer «los de N2» desde las matrículas de hoy.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        modelo = self.env.context.get("active_model")
        ids = self.env.context.get("active_ids") or []
        if modelo and ids and "partner_ids" in fields_list:
            registros = self.env[modelo].browse(ids)
            if modelo == "res.partner":
                contactos = registros
            elif hasattr(registros, "_gts_chatroom_contactos"):
                contactos = registros._gts_chatroom_contactos()
            else:
                contactos = self.env["res.partner"]
            res["partner_ids"] = [(6, 0, contactos.ids)]
        return res

    def accion_anadir(self):
        self.ensure_one()
        if not self.partner_ids:
            raise UserError(self.env._("No hay contactos que añadir."))
        lista = self.lista_id
        if not lista:
            if not self.nombre_nueva:
                raise UserError(
                    self.env._("Elige una lista o escribe el nombre de una nueva.")
                )
            lista = self.env["gts.chatroom.lista"].create({"name": self.nombre_nueva})
        if self.vaciar:
            lista.partner_ids = [(6, 0, self.partner_ids.ids)]
        else:
            lista.partner_ids = [(4, p.id) for p in self.partner_ids]
        return {
            "type": "ir.actions.act_window",
            "res_model": "gts.chatroom.lista",
            "res_id": lista.id,
            "view_mode": "form",
            "target": "current",
        }
