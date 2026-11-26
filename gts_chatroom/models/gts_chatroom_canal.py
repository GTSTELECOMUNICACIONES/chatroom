"""CANALES Y BANDEJAS (David, 25-11-2026): un punto central de
comunicaciones, como un Outlook con varios buzones.

UN CANAL ES UNA PASARELA (`mail.gateway`) DEL PORTAL: un número de WhatsApp,
una página de Facebook o una cuenta de Instagram (LinkedIn, cuando haya API).
Es lo que se cobra al cliente, así que cada conversación dice por cuál entró:
«WhatsApp · +34 6XX XX XX XX». El número sale de `alma_numero`, el que
identifica la cuenta ante el portal, y no del nombre que escriba alguien: un
nombre puede equivocarse, el número es el que funciona. «Comprobar con el
portal» confirma que el portal lo reconoce.

CADA CANAL TIENE SU BANDEJA en el menú «Bandejas», generada al crearlo o
renombrarlo: Sin atender · Para mí · Contestadas sin asignar · De otros
operadores · Con la IA · Resueltas · Todas. Son acciones normales sobre las
conversaciones, filtradas por canal y por cola: nada de JS que pueda dejar el
backend en blanco.
"""

import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from .mail_gateway_alma import _digitos

_logger = logging.getLogger(__name__)

TIPOS_CANAL = [
    ("whatsapp", "WhatsApp"),
    ("facebook", "Facebook Messenger"),
    ("instagram", "Instagram"),
    ("linkedin", "LinkedIn"),
]
CORTOS = {"whatsapp": "WhatsApp", "facebook": "Messenger", "instagram": "Instagram"}

MENU_BANDEJAS = "gts_chatroom.menu_chatroom_bandejas"

# (clave, nombre, dominio extra, secuencia). El orden es el de la pantalla.
BANDEJAS = [
    ("esperando", "Sin atender", [("alma_cola", "=", "esperando")], 10),
    (
        "mias",
        "Para mí",
        [("alma_es_mia", "=", True), ("alma_cola", "!=", "resuelta")],
        20,
    ),
    ("contestadas", "Contestadas sin asignar", [("alma_cola", "=", "contestada")], 30),
    (
        "otros",
        "De otros operadores",
        [("alma_cola", "=", "asignada"), ("alma_es_mia", "=", False)],
        40,
    ),
    ("ia", "Con la IA", [("alma_cola", "=", "ia")], 50),
    ("resueltas", "Resueltas", [("alma_cola", "=", "resuelta")], 60),
    ("todas", "Todas", [], 70),
]


def numero_legible(digitos):
    """«346XXXXXXXX» → «+34 6XX XX XX XX». Con phonenumbers si está (viene
    con Odoo); si no, +digitos."""
    if not digitos:
        return ""
    try:
        import phonenumbers

        numero = phonenumbers.parse(f"+{digitos}")
        return phonenumbers.format_number(
            numero, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
    except Exception:  # noqa: BLE001 - un formato raro se ensena tal cual
        return f"+{digitos}"


class MailGateway(models.Model):
    _inherit = "mail.gateway"

    canal_tipo = fields.Selection(
        TIPOS_CANAL,
        "Tipo de canal",
        default="whatsapp",
        help="Por dónde escriben los clientes. Cada canal es un número, una "
        "página o una cuenta, y es lo que se cobra.",
    )
    canal_numero = fields.Char(
        "Número o cuenta",
        compute="_compute_canal",
        help="Sale del número configurado, el que reconoce el portal; no del "
        "nombre del canal.",
    )
    canal_etiqueta = fields.Char("Canal", compute="_compute_canal")
    alma_crear_contactos = fields.Boolean(
        "Crear contactos automáticamente",
        default=False,
        help="Apagado: quien escribe y no está en Contactos entra como "
        "invitado, y se le hace ficha con «Nuevo contacto» desde la "
        "conversación. Encendido: se crea un contacto por cada número nuevo.",
    )
    plantilla_inicio_id = fields.Many2one(
        "gts.chatroom.plantilla",
        "Plantilla para empezar conversaciones",
        domain="[('gateway_id', '=', id), ('se_puede_enviar', '=', True)]",
        help="La que sale ya elegida en «Nueva conversación». Lo cómodo es una "
        "de utilidad tipo «Hola {{1}}, {{2}}», con el nombre del contacto en "
        "el 1 y el mensaje en el 2.",
    )
    bandeja_menu_id = fields.Many2one(
        "ir.ui.menu", "Menú de la bandeja", readonly=True, copy=False
    )

    @api.depends("canal_tipo", "alma_numero", "name", "gateway_type")
    def _compute_canal(self):
        for gateway in self:
            numero = (
                numero_legible(_digitos(gateway.alma_numero))
                if gateway.gateway_type == "alma"
                else ""
            )
            gateway.canal_numero = numero or False
            corto = CORTOS.get(gateway.canal_tipo) or dict(TIPOS_CANAL).get(
                gateway.canal_tipo, ""
            )
            gateway.canal_etiqueta = (
                " · ".join(p for p in (corto, numero) if p) or gateway.name
            )

    @api.constrains("alma_numero", "gateway_type", "canal_tipo")
    def _comprobar_numero(self):
        for gateway in self.filtered(
            lambda g: g.gateway_type == "alma" and g.canal_tipo == "whatsapp"
        ):
            digitos = _digitos(gateway.alma_numero)
            if gateway.alma_numero and not 8 <= len(digitos) <= 15:
                raise UserError(
                    self.env._(
                        "El número de WhatsApp «%s» no parece un número completo: "
                        "va con el prefijo del país y sin el +, por ejemplo "
                        "34 y el número, todo seguido.",
                        gateway.alma_numero,
                    )
                )

    def action_comprobar_con_el_portal(self):
        """Pregunta al portal por el número: si no es de la cuenta de la
        clave, lo dice (404 numero_desconocido). Solo lee."""
        self.ensure_one()
        Alma = self.env["mail.gateway.alma"]
        respuesta = Alma._alma_peticion(
            self,
            "GET",
            "/api/odoo/plantillas",
            params={"numero": _digitos(self.alma_numero)},
        )
        datos = Alma._alma_json(respuesta)
        if not Alma._alma_ok(respuesta, datos):
            raise UserError(Alma._alma_texto_error(respuesta, datos))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Canal comprobado"),
                "type": "success",
                "message": self.env._(
                    "El portal reconoce %s con la clave de este canal.",
                    self.canal_etiqueta,
                ),
            },
        }

    @api.model
    def _gts_abrir_portal(self):
        """El menú «Créditos»: la cuenta del cliente en el portal, donde está
        el monedero (David, 26-11-2026: a los meses nadie se acuerda del
        enlace). La ruta dentro del portal va en un parámetro para no tocar
        código si cambia."""
        canal = self.sudo().search(
            [
                ("gateway_type", "=", "alma"),
                ("integrated_webhook_state", "=", "integrated"),
                ("alma_url", "!=", False),
            ],
            limit=1,
        )
        if not canal:
            raise UserError(
                self.env._("No hay ningún canal del portal de Alma configurado.")
            )
        ruta = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("gts_chatroom.ruta_creditos", "")
        )
        return {
            "type": "ir.actions.act_url",
            "url": canal.alma_url.rstrip("/") + "/" + ruta.lstrip("/"),
            "target": "new",
        }

    # ------------------------------------------------------------------
    # Las bandejas
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        gateways = super().create(vals_list)
        gateways._gts_sincronizar_bandejas()
        return gateways

    def write(self, vals):
        res = super().write(vals)
        if {
            "name",
            "canal_tipo",
            "alma_numero",
            "gateway_type",
            "integrated_webhook_state",
        } & set(vals):
            self._gts_sincronizar_bandejas()
        return res

    def unlink(self):
        self._gts_quitar_bandeja()
        return super().unlink()

    @api.model
    def _gts_sincronizar_todas_las_bandejas(self):
        """Al actualizar el módulo: las de todos los canales del portal."""
        self.search([("gateway_type", "=", "alma")])._gts_sincronizar_bandejas()
        return True

    def _gts_quitar_bandeja(self):
        for gateway in self.sudo().filtered("bandeja_menu_id"):
            menu = gateway.bandeja_menu_id
            # `action` es un Reference: se recogen una a una, sin mapped.
            acciones = [
                m.action
                for m in menu.child_id
                if m.action and m.action._name == "ir.actions.act_window"
            ]
            menu.child_id.unlink()
            menu.unlink()
            for accion in acciones:
                accion.unlink()

    def _gts_sincronizar_bandejas(self):
        """Rehace la bandeja de cada canal del portal integrado: una sección
        en «Bandejas» con su nombre y su número, y debajo las vistas."""
        raiz = self.env.ref(MENU_BANDEJAS, raise_if_not_found=False)
        if not raiz:
            return
        vistas = [
            (
                "kanban",
                self.env.ref(
                    "gts_chatroom.discuss_channel_alma_kanban", raise_if_not_found=False
                ),
            ),
            (
                "list",
                self.env.ref(
                    "gts_chatroom.discuss_channel_alma_list", raise_if_not_found=False
                ),
            ),
        ]
        busqueda = self.env.ref(
            "gts_chatroom.discuss_channel_alma_search", raise_if_not_found=False
        )
        Accion = self.env["ir.actions.act_window"].sudo()
        Menu = self.env["ir.ui.menu"].sudo()
        for gateway in self.sudo():
            gateway._gts_quitar_bandeja()
            if (
                gateway.gateway_type != "alma"
                or gateway.integrated_webhook_state != "integrated"
            ):
                continue
            titulo = gateway.name or gateway.canal_etiqueta
            if gateway.canal_numero and gateway.canal_numero not in titulo:
                titulo = f"{titulo} · {gateway.canal_numero}"
            seccion = Menu.create(
                {"name": titulo, "parent_id": raiz.id, "sequence": gateway.id}
            )
            for clave, nombre, dominio, secuencia in BANDEJAS:
                accion = Accion.create(
                    {
                        "name": f"{nombre} · {titulo}",
                        "res_model": "discuss.channel",
                        "view_mode": "kanban,list",
                        "domain": repr(
                            [("alma_es", "=", True), ("gateway_id", "=", gateway.id)]
                            + dominio
                        ),
                        "context": repr(
                            {"gts_bandeja": clave, "gts_canal_id": gateway.id}
                        ),
                        "search_view_id": busqueda.id if busqueda else False,
                        "view_ids": [
                            (0, 0, {"sequence": n, "view_mode": modo, "view_id": v.id})
                            for n, (modo, v) in enumerate(vistas)
                            if v
                        ],
                    }
                )
                Menu.create(
                    {
                        "name": nombre,
                        "parent_id": seccion.id,
                        "sequence": secuencia,
                        "action": f"ir.actions.act_window,{accion.id}",
                    }
                )
            gateway.bandeja_menu_id = seccion

    @api.model
    def _gts_esperando_por_canal(self):
        """{id de canal: conversaciones sin atender}, para el contador del
        menú."""
        grupos = self.env["discuss.channel"]._read_group(
            [
                ("alma_es", "=", True),
                ("alma_cola", "=", "esperando"),
                ("alma_motivo_bloqueo", "!=", "spam"),
            ],
            ["gateway_id"],
            ["__count"],
        )
        return {gateway.id: total for gateway, total in grupos if gateway}


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    gts_canal = fields.Char("Canal", related="gateway_id.canal_etiqueta")
