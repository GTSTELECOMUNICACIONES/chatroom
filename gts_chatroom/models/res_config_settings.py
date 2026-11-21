from odoo import fields, models

from .discuss_channel import MINUTOS_AVISO, PARAM_MINUTOS, PARAM_RESPONSABLE


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    alma_minutos_aviso = fields.Integer(
        string="Minutos de espera antes de avisar",
        config_parameter=PARAM_MINUTOS,
        default=MINUTOS_AVISO,
    )
    alma_responsable_id = fields.Many2one(
        "res.users",
        string="Responsable de las esperas",
        config_parameter=PARAM_RESPONSABLE,
        domain=[("share", "=", False)],
    )
