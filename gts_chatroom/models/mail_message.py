from odoo import fields, models


class MailMessage(models.Model):
    _inherit = "mail.message"

    # El id del mensaje en el portal. Sirve para descartar repetidos: al
    # recuperar se pide con cinco minutos de margen a proposito, y el portal
    # puede reintentar un envio que si llego.
    alma_mensaje_id = fields.Char(index="btree_not_null", readonly=True)
