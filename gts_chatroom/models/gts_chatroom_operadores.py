"""LOS OPERADORES, AL PORTAL (Carlos y David, 26-11-2026).

Si el cliente escribe «pásame con David», el portal le asigna la conversación
a David en vez de mandarla a la bandeja de entrada. Para eso el portal tiene
que saber quiénes son: los miembros del canal, con TODAS las formas en que un
cliente los escribiría y el correo que viaja en `asignado_a`.

    PUT /api/odoo/operadores
    {numero, operadores: [{nombres: ["David", "David Fernández"], correo}]}
    -> 200 {guardados, reemplazados}

Es la lista COMPLETA: reemplaza la anterior. El portal rechaza una vacía
(borrar a todos por un fallo sería que «pásame con» deja de funcionar sin que
nadie sepa por qué): si un canal se queda sin miembros, no se manda nada.

Se empuja al cambiar los miembros o el nombre o el correo de uno, después del
commit, y una vez al día como red: lo rápido puede fallar, lo lento no.
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

CAMPOS_DEL_OPERADOR = {"name", "email", "login", "active"}

# NUNCA UN CAMPO GUARDADO EN res.users (26-11-2026). La 0.15.0 añadió
# `gts_chatroom_nombres` aquí: COPYE hizo el pull sin actualizar y la web
# entera dio 500, porque Odoo lee res_users en CADA petición y la columna no
# existía. Un campo nuevo en un modelo que se lee siempre (usuarios,
# contactos, compañía) convierte «pull sin actualizar» en caída total. Los
# nombres para los clientes, si hacen falta, en un modelo propio.


class ResUsers(models.Model):
    _inherit = "res.users"

    def _gts_nombres_para_clientes(self):
        """El nombre de pila y el completo: «David», «David Fernández»."""
        self.ensure_one()
        completo = (self.name or "").strip()
        pila = completo.split(" ")[0] if completo else ""
        return [n for n in dict.fromkeys([pila, completo]) if n]

    def write(self, vals):
        res = super().write(vals)
        if CAMPOS_DEL_OPERADOR & set(vals):
            canales = (
                self.env["mail.gateway"]
                .sudo()
                .search(
                    [
                        ("gateway_type", "=", "alma"),
                        ("member_ids", "in", self.ids),
                    ]
                )
            )
            canales._gts_enviar_operadores_tras_commit()
        return res


class MailGateway(models.Model):
    _inherit = "mail.gateway"

    def write(self, vals):
        res = super().write(vals)
        if "member_ids" in vals:
            self._gts_enviar_operadores_tras_commit()
        return res

    def _gts_enviar_operadores_tras_commit(self):
        ids = self.filtered(lambda g: g.gateway_type == "alma").ids
        if not ids:
            return
        registro = self.env.registry

        @self.env.cr.postcommit.add
        def _enviar():
            with registro.cursor() as cr:
                env = api.Environment(cr, self.env.uid, {})
                env["mail.gateway"].browse(ids)._gts_enviar_operadores()

    @api.model
    def _gts_enviar_operadores_a_todos(self):
        """El cron diario: la red por si un envío se perdió."""
        self.sudo().search(
            [
                ("gateway_type", "=", "alma"),
                ("integrated_webhook_state", "=", "integrated"),
            ]
        )._gts_enviar_operadores()
        return True

    def _gts_enviar_operadores(self):
        Alma = self.env["mail.gateway.alma"]
        Canal = self.env["discuss.channel"]
        for gateway in self.sudo():
            if (
                gateway.gateway_type != "alma"
                or gateway.integrated_webhook_state != "integrated"
            ):
                continue
            operadores = [
                {
                    "nombres": usuario._gts_nombres_para_clientes(),
                    "correo": (Canal._alma_correo(usuario) or "").lower(),
                }
                for usuario in gateway.member_ids.filtered(
                    lambda u: u.active and not u.share
                )
            ]
            operadores = [o for o in operadores if o["correo"] and o["nombres"]]
            if not operadores:
                _logger.warning(
                    "ChatRoom: el canal %s no tiene operadores; no se manda la "
                    "lista (el portal rechaza una vacía)",
                    gateway.id,
                )
                continue
            try:
                respuesta = Alma._alma_peticion(
                    gateway,
                    "PUT",
                    "/api/odoo/operadores",
                    json={
                        "numero": Alma._alma_numero_de(gateway),
                        "operadores": operadores,
                    },
                )
            except Exception:  # noqa: BLE001 - lo recoge el cron de mañana
                _logger.warning(
                    "ChatRoom: no se pudo mandar la lista de operadores de %s",
                    gateway.id,
                    exc_info=True,
                )
                continue
            datos = Alma._alma_json(respuesta)
            if not Alma._alma_ok(respuesta, datos):
                _logger.warning(
                    "ChatRoom: el portal no aceptó los operadores de %s: %s",
                    gateway.id,
                    Alma._alma_texto_error(respuesta, datos),
                )
