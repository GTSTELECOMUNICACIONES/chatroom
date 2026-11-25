"""ESCRIBIR A UN CONTACTO QUE NUNCA HA ESCRITO (David, 25-11-2026).

La pasarela de la OCA solo deja «Enviar a pasarela» a un seguidor de la ficha
que tenga canal (`res.partner.gateway.channel`), y ese enlace lo creaba el
ChatRoom al ENTRAR un mensaje. A quien no ha escrito nunca por este número no
se le podía escribir desde su ficha.

- «Enlazar con WhatsApp» crea el enlace con su teléfono en cada pasarela del
  portal y lo hace seguidor de su propia ficha.
- «Enviar plantilla de WhatsApp» abre un comunicado solo para él: fuera de las
  24 horas desde su último mensaje, Meta no deja mandar texto libre, solo una
  plantilla aprobada.
"""

from odoo import models
from odoo.exceptions import UserError

from .mail_gateway_alma import _digitos


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _gts_pasarelas_alma(self):
        return (
            self.env["mail.gateway"]
            .sudo()
            .search(
                [
                    ("gateway_type", "=", "alma"),
                    ("integrated_webhook_state", "=", "integrated"),
                ]
            )
        )

    def accion_enlazar_whatsapp(self):
        pasarelas = self._gts_pasarelas_alma()
        if not pasarelas:
            raise UserError(
                self.env._("No hay ninguna pasarela del portal de Alma integrada.")
            )
        Enlace = self.env["res.partner.gateway.channel"].sudo()
        enlazados, sin_telefono = 0, []
        for partner in self:
            telefono = _digitos(partner.phone_sanitized)
            if not telefono:
                sin_telefono.append(partner.display_name)
                continue
            for gateway in pasarelas:
                existe = Enlace.search(
                    [
                        ("gateway_id", "=", gateway.id),
                        "|",
                        ("partner_id", "=", partner.id),
                        ("gateway_token", "=", telefono),
                    ],
                    limit=1,
                )
                if not existe:
                    Enlace.create(
                        {
                            "partner_id": partner.id,
                            "gateway_id": gateway.id,
                            "gateway_token": telefono,
                        }
                    )
                    enlazados += 1
            # Para que salga en el selector de «Enviar a pasarela» de su ficha.
            partner.message_subscribe(partner_ids=partner.ids)
        mensaje = self.env._("%s contactos enlazados con WhatsApp.", enlazados)
        if sin_telefono:
            mensaje += " " + self.env._(
                "Sin teléfono válido: %s.", ", ".join(sin_telefono[:10])
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("WhatsApp"),
                "type": "warning" if sin_telefono else "success",
                "message": mensaje,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def accion_enviar_plantilla_whatsapp(self):
        nombre = (
            self.env._("WhatsApp a %s", self.display_name)
            if len(self) == 1
            else self.env._("WhatsApp a %s contactos", len(self))
        )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Enviar plantilla de WhatsApp"),
            "res_model": "gts.chatroom.comunicado",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_name": nombre,
                "default_partner_ids": [(6, 0, self.ids)],
            },
        }
