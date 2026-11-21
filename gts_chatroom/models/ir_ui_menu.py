import logging

from odoo import models

_logger = logging.getLogger(__name__)

MENU_ESPERANDO = "gts_chatroom.menu_chatroom_esperando"


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    def load_web_menus(self, debug):
        """El contador de «Esperando» en el menu, sin una linea de JS.

        Se calcula en el servidor cada vez que el navegador pide los menus
        (`/web/webclient/load_menus`, sin cache, en cada carga de pagina): no
        es en vivo, se actualiza al recargar. Un contador en la barra superior
        si lo seria, pero un fallo ahi deja el backend entero en blanco y ya
        nos paso una vez.

        `load_menus` esta en ormcache; este no, y construye diccionarios
        nuevos, asi que tocar el nombre aqui no ensucia la cache."""
        menus = super().load_web_menus(debug)
        menu = next(
            (m for m in menus.values() if m.get("xmlid") == MENU_ESPERANDO), None
        )
        if not menu:
            return menus
        # UN CONTADOR NO PUEDE ROMPER LOS MENUS: si falla, el menu sale sin
        # numero. El savepoint, porque un error de base de datos sin el deja
        # la transaccion abortada para todo lo que venga detras.
        try:
            with self.env.cr.savepoint():
                total = self.env["discuss.channel"].search_count(
                    [("alma_es", "=", True), ("alma_cola", "=", "esperando")]
                )
        except Exception:
            _logger.exception("ChatRoom: no se pudo contar las esperas")
            return menus
        if total:
            menu["name"] = f"{menu['name']} ({total})"
        return menus
