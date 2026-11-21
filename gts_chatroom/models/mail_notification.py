from odoo import fields, models

# El orden en que avanza un mensaje que sale. «fallido» va aparte: puede
# llegar despues de «salio», pero no despues de «entregado».
ESTADOS = ["en_cola", "salio", "entregado", "leido"]

# NO SE TRADUCEN A LOS DE ODOO: se guardan en campo propio. En Odoo la clave
# `pending` se ensena «Enviado» y `sent` se ensena «Entregado», asi que mapear
# a ciegas enseña «Entregado» de un mensaje que solo ha salido. Lo de abajo es
# solo para que el icono del chat diga algo razonable.
A_ODOO = {
    "en_cola": "process",
    "salio": "pending",
    "entregado": "sent",
    "leido": "sent",
    "fallido": "exception",
}


class MailNotification(models.Model):
    _inherit = "mail.notification"

    alma_estado = fields.Selection(
        [
            ("en_cola", "En cola"),
            ("salio", "Salio"),
            ("entregado", "Entregado"),
            ("leido", "Leido"),
            ("fallido", "Fallido"),
        ],
        readonly=True,
    )
    alma_motivo = fields.Char(readonly=True)

    def _alma_avanzar(self, estado, motivo=False, detalle=False, frase=False):
        """Pone el estado si es MAS AVANZADO que el que hay. Los acuses no
        llegan en orden: un «entregado» puede entrar antes que su «salio»."""
        cambiados = self.browse()
        for notification in self:
            actual = notification.alma_estado
            if estado == "fallido":
                if actual in ("entregado", "leido", "fallido"):
                    continue
            elif estado not in ESTADOS:
                continue
            elif actual == "fallido" or (
                actual in ESTADOS and ESTADOS.index(actual) >= ESTADOS.index(estado)
            ):
                continue
            vals = {
                "alma_estado": estado,
                "notification_status": A_ODOO[estado],
            }
            if estado == "fallido":
                vals.update(
                    {
                        "alma_motivo": motivo or False,
                        "failure_type": "unknown",
                        "failure_reason": self.env[
                            "mail.gateway.alma"
                        ]._alma_texto_motivo(motivo, frase or detalle),
                    }
                )
            notification.sudo().write(vals)
            cambiados |= notification
        return cambiados
