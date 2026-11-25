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
    # LISTA DINÁMICA (David, 25-11-2026, como las del portal): entran solos
    # los contactos con estas etiquetas, y además se añade o se quita a mano
    # a quien haga falta (José, que es empleado y no lleva la de N2). La
    # escuela mantiene sola las etiquetas de nivel de los tutores.
    etiqueta_ids = fields.Many2many(
        "res.partner.category",
        "gts_chatroom_lista_etiqueta_rel",
        "lista_id",
        "etiqueta_id",
        string="Entran los contactos con",
        help="Cualquier contacto con estas etiquetas está en la lista, y deja "
        "de estarlo cuando se le quita la etiqueta.",
    )
    regla = fields.Selection(
        [("alguna", "Alguna de las etiquetas"), ("todas", "Todas las etiquetas")],
        "Regla",
        default="alguna",
        required=True,
    )
    partner_ids = fields.Many2many(
        "res.partner",
        "gts_chatroom_lista_partner_rel",
        "lista_id",
        "partner_id",
        string="Añadidos a mano",
    )
    excluido_ids = fields.Many2many(
        "res.partner",
        "gts_chatroom_lista_excluido_rel",
        "lista_id",
        "partner_id",
        string="Quitados a mano",
        help="No reciben aunque tengan la etiqueta.",
    )
    miembro_ids = fields.Many2many(
        "res.partner",
        string="Quiénes están",
        compute="_compute_miembros",
        help="Los de las etiquetas, más los añadidos, menos los quitados. Se "
        "calcula al momento: es la lista que recibe el comunicado.",
    )
    contactos = fields.Integer("Contactos", compute="_compute_miembros")
    sin_telefono = fields.Integer(
        "Sin teléfono válido",
        compute="_compute_miembros",
        help="Contactos sin un teléfono que se pueda usar en WhatsApp: no les "
        "llegará ningún comunicado.",
    )
    nota = fields.Text("Para qué es")
    active = fields.Boolean(default=True)

    def _de_las_etiquetas(self):
        self.ensure_one()
        if not self.etiqueta_ids:
            return self.env["res.partner"]
        if self.regla == "todas":
            dominio = [("category_id", "in", e.id) for e in self.etiqueta_ids]
        else:
            dominio = [("category_id", "in", self.etiqueta_ids.ids)]
        return self.env["res.partner"].search(dominio)

    @api.depends("etiqueta_ids", "regla", "partner_ids", "excluido_ids")
    def _compute_miembros(self):
        for lista in self:
            miembros = (
                lista._de_las_etiquetas() | lista.partner_ids
            ) - lista.excluido_ids
            lista.miembro_ids = miembros
            lista.contactos = len(miembros)
            lista.sin_telefono = len(miembros.filtered(lambda p: not p.phone_sanitized))

    def accion_ver_sin_telefono(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Sin teléfono válido · %s", self.name),
            "res_model": "res.partner",
            "view_mode": "list,form",
            "domain": [
                ("id", "in", self.miembro_ids.ids),
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
