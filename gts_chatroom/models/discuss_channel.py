from odoo import api, fields, models

RELEVO = [
    ("ia", "La IA"),
    ("humano", "Una persona"),
    ("resuelta", "Resuelta"),
]

# LA COLA SE DERIVA AQUI, de las piezas que manda el portal. Si el portal
# mandara la etiqueta ya calculada, se perderia lo que el no previo. Y se
# perdia: «humano, sin asignar, ya contestada» -el dueno contesta desde su
# movil antes de que nadie la coja- no cabia en cuatro valores y tuvo el
# buzon mudo un dia entero (Carlos, 19-11-2026). Las cuatro ultimas
# conversaciones archivadas en GTS Racing acabaron asi.
COLA = [
    ("ia", "Con la IA"),
    ("esperando", "Esperando a una persona"),
    ("asignada", "La atiende una persona"),
    ("contestada", "Contestada, sin asignar"),
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
    alma_espera = fields.Boolean(string="Espera respuesta", readonly=True)
    alma_motivo_relevo = fields.Char(string="Motivo del relevo", readonly=True)
    alma_cola = fields.Selection(
        COLA, string="Cola", compute="_compute_alma_cola", store=True, index=True
    )
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

    @api.depends("alma_relevo", "alma_asignado_a", "alma_espera")
    def _compute_alma_cola(self):
        for channel in self:
            if channel.alma_relevo != "humano":
                channel.alma_cola = channel.alma_relevo or False
            elif channel.alma_asignado_a:
                channel.alma_cola = "asignada"
            elif channel.alma_espera:
                channel.alma_cola = "esperando"
            else:
                channel.alma_cola = "contestada"

    def _alma_aplicar_estado(self, estado, emitido_en):
        """Copia las piezas del relevo que manda el portal, si no son mas
        viejas que las que ya hay. Se compara por `emitido_en`, el reloj del
        portal al emitir, nunca por la fecha del mensaje."""
        self.ensure_one()
        relevo = estado.get("relevo")
        espera = bool(estado.get("espera"))
        if relevo == "esperando":
            # La forma de la version 0.1 del contrato, por si llega alguna.
            relevo, espera = "humano", True
        if relevo not in dict(RELEVO):
            return
        if self.alma_estado_en and emitido_en < self.alma_estado_en:
            return
        antes = self.alma_cola
        self.sudo().write(
            {
                "alma_relevo": relevo,
                "alma_asignado_a": estado.get("asignado_a") or False,
                "alma_espera": espera,
                "alma_motivo_relevo": estado.get("motivo_relevo") or False,
                "alma_estado_en": emitido_en,
            }
        )
        # «Esperando desde» se fija al ENTRAR en espera y no se mueve con cada
        # mensaje: la cola se ordena por cuanto lleva esperando.
        if self.alma_cola == "esperando" and antes != "esperando":
            self.sudo().alma_esperando_desde = emitido_en
        elif self.alma_cola != "esperando":
            self.sudo().alma_esperando_desde = False

    def action_alma_abrir(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/odoo/action-mail.action_discuss?active_id={self.id}",
            "target": "self",
        }
