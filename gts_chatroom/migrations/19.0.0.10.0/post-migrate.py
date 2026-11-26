"""Las plantillas que ya estaban copiadas se aplican a Contactos, como las
nuevas: así sale «Enviar WhatsApp» en la ficha del contacto desde el primer
día. Una sola vez; luego cada una se cambia a mano."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Plantilla = env["gts.chatroom.plantilla"].with_context(active_test=False)
    Plantilla.search([("model_id", "=", False)]).write(
        {"model_id": env.ref("base.model_res_partner").id}
    )
