{
    "name": "ChatRoom — guardar adjuntos en el expediente",
    "summary": "«Guardar en el expediente» junto a «Descargar» en los adjuntos "
    "de las conversaciones del ChatRoom",
    "version": "18.0.0.1.0",
    "category": "Discuss",
    "author": "GTS Telecomunicaciones",
    "website": "https://github.com/GTSTELECOMUNICACIONES/chatroom",
    # Puente a un módulo AGPL (gts_chatroom, que hereda la AGPL de
    # mail_gateway): nace AGPL, como manda LICENCIAS.md de gts-abogados
    # («el puente de OCA nace AGPL desde el primer día»). gts_expediente no
    # depende de esto y sigue siendo suyo.
    "license": "AGPL-3",
    # SIN el DMS: si está `gts_expediente_archivo` (que trae
    # `project.project.gts_archivar`), se archiva también en la carpeta; si
    # no, el adjunto queda en el expediente y en su historial.
    "depends": ["gts_chatroom", "gts_expediente"],
    "data": [
        "security/ir.model.access.csv",
        "wizards/guardar_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "gts_chatroom_expediente/static/src/attachment_list_patch.js",
            "gts_chatroom_expediente/static/src/attachment_list_patch.xml",
        ],
    },
}
