import logging

from odoo import models

_logger = logging.getLogger(__name__)

MENU_ESPERANDO = "gts_chatroom.menu_chatroom_esperando"
# La versión va en «Configuración», no en el nombre de la aplicación: en el
# icono del inicio quedaba feo (David, 26-11-2026).
MENU_VERSION = "gts_chatroom.menu_chatroom_config"


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
        self._alma_version_en_el_menu(menus)
        self._gts_contadores_de_bandejas(menus)
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

    def _alma_version_en_el_menu(self, menus):
        """La version dentro del modulo, en el menu «Configuración ·
        v0.11.5». Cuando un cliente llama con un problema, se le pregunta y
        la ve sin entrar en Aplicaciones (David, 21-11-2026). Antes iba en el
        nombre de la aplicacion y salia tambien en el icono del inicio, que
        quedaba feo (26-11-2026). Sale de la version INSTALADA, no del
        fichero: es la que de verdad corre."""
        raiz = next((m for m in menus.values() if m.get("xmlid") == MENU_VERSION), None)
        if not raiz:
            return
        try:
            with self.env.cr.savepoint():
                version = (
                    self.env["ir.module.module"]
                    .sudo()
                    .search([("name", "=", "gts_chatroom")], limit=1)
                    .latest_version
                )
        except Exception:
            _logger.exception("ChatRoom: no se pudo leer la version")
            return
        if version:
            corta = version.split(".", 2)[2] if version.count(".") >= 3 else version
            raiz["name"] = f"{raiz['name']} · v{corta}"

    def _gts_contadores_de_bandejas(self, menus):
        """«Sin atender (3)» en la bandeja de cada canal, y el total en el
        título del canal. Mismas reglas que el de «Esperando»: si falla, los
        menús salen sin número."""
        try:
            with self.env.cr.savepoint():
                Gateway = self.env["mail.gateway"].sudo()
                por_canal = Gateway._gts_esperando_por_canal()
                canales = Gateway.search(
                    [("gateway_type", "=", "alma"), ("bandeja_menu_id", "!=", False)]
                )
                for gateway in canales:
                    total = por_canal.get(gateway.id)
                    if not total:
                        continue
                    seccion = gateway.bandeja_menu_id
                    sin_atender = seccion.child_id.filtered(lambda m: m.sequence == 10)[
                        :1
                    ]
                    for menu in (seccion, sin_atender):
                        datos = menus.get(menu.id)
                        if datos:
                            datos["name"] = f"{datos['name']} ({total})"
        except Exception:
            _logger.exception("ChatRoom: no se pudo contar las bandejas")
