"""Los borradores que tenían los datos en un texto «uno por línea» pasan a
una casilla por dato (25-11-2026)."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for comunicado in env["gts.chatroom.comunicado"].search(
        [("state", "=", "borrador")]
    ):
        viejos = [
            linea.strip()
            for linea in (comunicado.parametros or "").splitlines()
            if linea.strip()
        ]
        comunicado.dato_ids = [(5, 0, 0)] + [
            (
                0,
                0,
                {
                    "posicion": n,
                    "valor": viejos[n - 1] if n <= len(viejos) else "",
                },
            )
            for n in range(1, (comunicado.variables or 0) + 1)
        ]
