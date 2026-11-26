"""Los datos que ya salían del NOMBRE del contacto pasan a «solo el nombre
de pila» (David, 26-11-2026): es lo que se quería al poner «Hola {{1}}». A
una empresa se le sigue poniendo el nombre entero."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["gts.chatroom.plantilla.variable"].search(
        [("origen", "=", "campo"), ("campo", "in", ["name", "partner_id.name"])]
    ).write({"formato": "nombre_pila"})
