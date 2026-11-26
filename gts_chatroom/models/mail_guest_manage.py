from odoo import models


class MailGuestManage(models.TransientModel):
    """«Nuevo contacto» desde la conversación (asistente de la OCA): la ficha
    nace con el TELÉFONO del que escribe, no solo con su nombre. Sin él, la
    siguiente conversación de ese número no la encontraría."""

    _inherit = "mail.guest.manage"

    def _get_partner_vals(self):
        vals = super()._get_partner_vals()
        token = self.guest_id.gateway_token
        if token and self.guest_id.gateway_id.gateway_type == "alma":
            vals["phone"] = f"+{token}"
        return vals
