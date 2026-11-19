import secrets

from odoo import api, fields, models
from odoo.exceptions import UserError


class MailGateway(models.Model):
    _inherit = "mail.gateway"

    gateway_type = fields.Selection(
        selection_add=[("alma", "Alma (portal)")], ondelete={"alma": "cascade"}
    )
    # LA URL DE SALIDA VA EN UN CAMPO, NUNCA EN EL CODIGO. Decision de David:
    # el repositorio es publico, y un modulo que se lleven no sirve de nada sin
    # saber contra que conecta.
    alma_url = fields.Char(
        string="URL del portal",
        help="Direccion base del portal de Alma, sin la ruta. "
        "Ejemplo: https://portal.ejemplo.com",
    )
    # El portal identifica la cuenta POR EL NUMERO EMISOR, no por la clave: la
    # clave autentica, el numero identifica. Si lo que identifica viajara en lo
    # que manda el cliente, un error de configuracion le cobraria a otro.
    alma_numero = fields.Char(
        string="Numero de WhatsApp",
        help="Numero emisor tal y como lo tiene el portal, con prefijo y sin +.",
    )
    alma_url_entrada = fields.Char(
        string="URL de entrada",
        related="webhook_url",
        help="La que hay que dar de alta en el portal para que mande aqui "
        "los mensajes.",
    )
    alma_ultimo_evento = fields.Datetime(
        string="Ultimo evento recibido",
        readonly=True,
        help="Fecha de emision del ultimo evento procesado. Al recuperar se "
        "pide desde cinco minutos antes.",
    )

    @api.model_create_multi
    def create(self, mvals):
        for vals in mvals:
            if vals.get("gateway_type") == "alma":
                self._alma_completar_claves(vals)
        return super().create(mvals)

    @api.model
    def _alma_completar_claves(self, vals):
        # Las claves de entrada las inventa Odoo y se copian al portal. Una
        # clave puesta a mano acaba siendo «1234» o la misma en dos clientes.
        vals.setdefault("webhook_key", secrets.token_urlsafe(24))
        vals.setdefault("webhook_secret", secrets.token_urlsafe(32))

    def action_alma_regenerar_claves(self):
        """Cambia la URL de entrada y el secreto. El portal deja de poder
        entrar hasta que se le copien los nuevos: es lo que se quiere si se ha
        filtrado alguno."""
        for gateway in self.filtered(lambda g: g.gateway_type == "alma"):
            gateway.write(
                {
                    "webhook_key": secrets.token_urlsafe(24),
                    "webhook_secret": secrets.token_urlsafe(32),
                }
            )

    def action_alma_recuperar(self):
        """Pide al portal lo que se haya perdido. Los repetidos no duelen —se
        descartan por el id del mensaje— y un hueco si."""
        self.ensure_one()
        if self.gateway_type != "alma":
            raise UserError(self.env._("Esta pasarela no es del portal de Alma."))
        procesados = self.env["mail.gateway.alma"]._alma_recuperar(self)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("ChatRoom"),
                "type": "success",
                "message": self.env._(
                    "Recuperados %(n)s eventos del portal.", n=procesados
                ),
            },
        }
