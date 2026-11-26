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

    # El id del mensaje en el portal, SOLO en lo que ENTRA del portal. Sirve
    # para descartar repetidos (al recuperar se pide con cinco minutos de
    # margen a proposito) y, junto con `alma_origen`, para que lo que entra no
    # vuelva a salir nunca (`_send`). Lo que sale guarda el id del portal en
    # `mail.notification.gateway_message_id`, no aqui (desde 0.3.7).
    alma_mensaje_id = fields.Char(index="btree_not_null", readonly=True)
    alma_origen = fields.Selection(ORIGENES, readonly=True)

    # UNA PLANTILLA DESDE LA CONVERSACIÓN (26-11-2026). Un mensaje que lleva
    # plantilla sale por `/enviar` con `plantilla` y no con `texto`: fuera de
    # las 24 horas Meta solo deja plantillas. El cuerpo es como quedó, para
    # que se lea en la conversación.
    alma_plantilla_id = fields.Many2one(
        "gts.chatroom.plantilla", readonly=True, ondelete="set null"
    )
    alma_parametros = fields.Json(readonly=True)
