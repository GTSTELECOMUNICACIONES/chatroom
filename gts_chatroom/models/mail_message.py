from odoo import fields, models

# De donde viene un mensaje, tal y como lo manda el portal. Se GUARDA aunque
# para ensenarlo se agrupen: «operador» y «movil» salen los dos como la
# empresa, pero saber por donde contesto alguien explica por que un mensaje no
# esta donde se le busca (Carlos, 19-11-2026).
ORIGENES = [
    ("contacto", "El cliente"),
    ("ia", "La IA"),
    ("operador", "Equipo, desde el ChatRoom"),
    ("movil", "Equipo, desde su móvil"),
    ("sistema", "Aviso automático"),
]


class MailMessage(models.Model):
    _inherit = "mail.message"

    # El id del mensaje en el portal. Sirve para descartar repetidos: al
    # recuperar se pide con cinco minutos de margen a proposito, y el portal
    # devuelve por el webhook lo que escribe un operador desde Odoo.
    alma_mensaje_id = fields.Char(index="btree_not_null", readonly=True)
    alma_origen = fields.Selection(ORIGENES, readonly=True)
