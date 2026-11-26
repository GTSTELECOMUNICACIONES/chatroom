{
    "name": "ChatRoom",
    "summary": "WhatsApp atendido desde Odoo, a traves del portal de Alma",
    "description": """
EL TRANSPORTE ENTRE ODOO Y EL PORTAL DE ALMA.

Odoo nunca habla con Meta. Los mensajes van Odoo -> portal -> Gupshup -> Meta,
y vuelven por el mismo camino. El portal es el que sabe el saldo, el relevo
entre la IA y las personas, y las reglas de Meta.

Este modulo anade a la pasarela de la OCA (`mail_gateway`) un tipo nuevo,
`alma`, sin tocar nada suyo. Las conversaciones son canales de Discuss y los
mensajes son mensajes normales de Odoo: si se desinstala, el historial se
queda.

La direccion del portal y las claves van en la ficha de la pasarela, nunca en
el codigo: el repositorio es publico.
""",
    "version": "19.0.0.10.0",
    "category": "Discuss",
    "author": "GTS Telecomunicaciones",
    "website": "https://github.com/GTSTELECOMUNICACIONES/chatroom",
    # Heredada de mail_gateway, que es AGPL-3.
    "license": "AGPL-3",
    "depends": ["mail_gateway", "phone_validation"],
    "data": [
        "security/ir.model.access.csv",
        "data/res_partner_data.xml",
        "data/ir_cron_data.xml",
        "data/rotulos_data.xml",
        "views/mail_gateway_views.xml",
        "views/discuss_channel_views.xml",
        "views/res_config_settings_views.xml",
        "views/comunicados_views.xml",
        "views/res_partner_views.xml",
        "views/chats_views.xml",
        "views/nueva_conversacion_views.xml",
        "views/menus.xml",
        "data/bandejas_data.xml",
        "data/plantillas_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "gts_chatroom/static/src/discuss_chatroom.js",
            "gts_chatroom/static/src/discuss_chatroom.xml",
            "gts_chatroom/static/src/discuss_chatroom.css",
        ],
    },
    "application": True,
}
