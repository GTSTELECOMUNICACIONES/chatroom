from odoo import api, fields, models

RELEVO = [
    ("ia", "La atiende la IA"),
    ("esperando", "Esperando a una persona"),
    ("humano", "La atiende una persona"),
    ("resuelta", "Resuelta"),
]


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    alma_es = fields.Boolean(
        string="Del portal de Alma",
        compute="_compute_alma_es",
        store=True,
    )
    alma_conversacion_id = fields.Char(
        string="Conversacion en el portal", index=True, readonly=True
    )
    # EL RELEVO MANDA EL PORTAL; ESTO ES UNA COPIA PARA PINTAR. Se cura sola:
    # cada mensaje entrante trae el estado dentro, asi que perder un aviso no
    # la deja mintiendo para siempre. Hueco aceptado: una conversacion sin
    # trafico no se cura, pero es justo la que nadie mira.
    alma_relevo = fields.Selection(RELEVO, string="Relevo", readonly=True)
    alma_asignado_a = fields.Char(string="La atiende", readonly=True)
    alma_estado_en = fields.Datetime(
        string="Estado a fecha de",
        readonly=True,
        help="Emision del evento que trajo el estado. Uno mas viejo no lo pisa: "
        "los eventos pueden llegar desordenados.",
    )
    alma_esperando_desde = fields.Datetime(string="Esperando desde", readonly=True)
    alma_ultimo_mensaje = fields.Datetime(string="Ultimo mensaje", readonly=True)

    @api.depends("gateway_id.gateway_type")
    def _compute_alma_es(self):
        for channel in self:
            channel.alma_es = channel.gateway_id.gateway_type == "alma"

    def _alma_aplicar_estado(self, estado, emitido_en):
        """Copia el estado del relevo que manda el portal, si no es mas viejo
        que el que ya hay."""
        self.ensure_one()
        relevo = estado.get("relevo")
        if relevo not in dict(RELEVO):
            return
        if self.alma_estado_en and emitido_en < self.alma_estado_en:
            return
        vals = {
            "alma_relevo": relevo,
            "alma_asignado_a": estado.get("asignado_a") or False,
            "alma_estado_en": emitido_en,
        }
        # «Esperando desde» se fija al ENTRAR en espera y no se mueve con cada
        # mensaje: la cola se ordena por cuanto lleva esperando, no por el
        # ultimo que escribio.
        if relevo == "esperando" and self.alma_relevo != "esperando":
            vals["alma_esperando_desde"] = emitido_en
        elif relevo != "esperando":
            vals["alma_esperando_desde"] = False
        self.sudo().write(vals)

    def action_alma_abrir(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/odoo/action-mail.action_discuss?active_id={self.id}",
            "target": "self",
        }
