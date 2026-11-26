from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        """Cómo se identifica este usuario ante el portal, para que el
        lateral de Chats sepa qué conversaciones son «Para mí» (el portal
        guarda en `asignado_a` el correo de quien la coge)."""
        result = super().session_info()
        if self.env.user._is_internal():
            result["gts_chatroom_correo"] = (
                self.env["discuss.channel"]._alma_correo(self.env.user) or ""
            ).lower()
        return result
