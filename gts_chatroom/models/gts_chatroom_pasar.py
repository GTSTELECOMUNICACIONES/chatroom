"""PASAR UNA CONVERSACIÓN A OTRA PERSONA (David, 26-11-2026).

Es el relevo «humano» del portal con el correo de esa persona en
`asignado_a` (`_alma_cambiar_relevo(para=…)`): pasa a su «Para mí», entra
como miembro y queda la línea «X pasa la conversación a Y». Si esa persona
no es miembro de la cuenta en el portal, el portal lo dice y la conversación
se queda sin dueño, a la vista.
"""

from odoo import fields, models


class GtsChatroomPasar(models.TransientModel):
    _name = "gts.chatroom.pasar"
    _description = "Pasar una conversación a otra persona"

    canal_id = fields.Many2one(
        "discuss.channel", "Conversación", required=True, ondelete="cascade"
    )
    usuario_id = fields.Many2one(
        "res.users",
        "Pasarla a",
        required=True,
        domain=lambda self: [
            ("share", "=", False),
            ("id", "!=", self.env.uid),
            (
                "all_group_ids",
                "in",
                self.env.ref("mail_gateway.gateway_user").ids,
            ),
        ],
    )

    def accion_pasar(self):
        self.ensure_one()
        resultado = self.canal_id.with_context(
            gts_desde_chats=True
        )._alma_cambiar_relevo("humano", para=self.usuario_id)
        # Un aviso del portal (no es miembro…) sale en vez de cerrar a secas.
        return resultado or {"type": "ir.actions.act_window_close"}
