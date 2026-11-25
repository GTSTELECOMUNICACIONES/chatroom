"""COMUNICADOS: una plantilla aprobada a una o varias listas (David,
25-11-2026). Sale por `POST /api/odoo/enviar/lote` del portal
(docs/contrato-plantillas-y-comunicados.md).

LO QUE DECIDE EL PORTAL Y NO ODOO, a proposito (Carlos):
- el RITMO: Odoo manda la lista entera en lotes de hasta 300 y el portal
  reparte los envios; dos ritmos que no se conocen acaban pasandose;
- las BAJAS, segun la categoria de la plantilla, y los bloqueados: vuelven
  rechazados en el acto, antes de mandar nada;
- el SALDO, una vez para el lote entero: media lista avisada es peor que
  ninguna.

EL ENVIO VA DESPUES DEL COMMIT, como el de un mensaje (ver el bucle de eco
del 21-11-2026 en el LEEME): si Odoo reintentara la transaccion, un POST
dentro de ella repetiria el comunicado entero, y cada plantilla se paga. El
boton deja el comunicado en «Enviando» y el POST sale una vez, guardado ya.

El seguimiento de cada destinatario son los acuses de siempre, casados por el
`mensaje_id` que devuelve el lote. No hay un contador aparte en el portal: dos
sitios que cuentan lo mismo acaban diciendo cosas distintas.
"""

import logging
import re

from odoo import api, fields, models
from odoo.exceptions import UserError

from .mail_gateway_alma import _digitos

_logger = logging.getLogger(__name__)

# El tope del portal por peticion (Carlos: 300 s de funcion, 0,41 s por envio).
POR_LOTE = 300
# El lote valida cientos de contactos antes de contestar: mas que un mensaje.
TIMEOUT_LOTE = 60

ESTADOS_DESTINATARIO = [
    ("pendiente", "Pendiente"),
    ("sin_telefono", "Sin teléfono válido"),
    ("rechazado", "Rechazado por el portal"),
    ("en_cola", "En cola"),
    ("salio", "Salió"),
    ("entregado", "Entregado"),
    ("leido", "Leído"),
    ("fallido", "Falló"),
]
# El orden en que avanza un envio; «fallido» va aparte.
AVANCE = ["en_cola", "salio", "entregado", "leido"]


class GtsChatroomComunicado(models.Model):
    _name = "gts.chatroom.comunicado"
    _description = "Comunicado de WhatsApp"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char("Asunto", required=True, tracking=True)
    gateway_id = fields.Many2one(
        "mail.gateway",
        "Pasarela",
        required=True,
        domain=[("gateway_type", "=", "alma")],
        default=lambda self: self.env["mail.gateway"].search(
            [
                ("gateway_type", "=", "alma"),
                ("integrated_webhook_state", "=", "integrated"),
            ],
            limit=1,
        ),
    )
    plantilla_id = fields.Many2one(
        "gts.chatroom.plantilla",
        "Plantilla",
        required=True,
        tracking=True,
        domain="[('gateway_id', '=', gateway_id), ('se_puede_enviar', '=', True)]",
    )
    categoria_texto = fields.Char(related="plantilla_id.categoria_texto")
    es_marketing = fields.Boolean(compute="_compute_es_marketing")
    variables = fields.Integer(related="plantilla_id.variables_cuerpo")
    parametros = fields.Text(
        "Datos de la plantilla",
        help="Uno por línea, en orden: la primera línea es el {{1}}, la segunda "
        "el {{2}}… WhatsApp no deja saltos de línea dentro de un dato.",
    )
    vista_previa = fields.Text("Así llegará", compute="_compute_vista_previa")
    lista_ids = fields.Many2many(
        "gts.chatroom.lista",
        "gts_chatroom_comunicado_lista_rel",
        "comunicado_id",
        "lista_id",
        string="Listas",
    )
    partner_ids = fields.Many2many(
        "res.partner",
        "gts_chatroom_comunicado_partner_rel",
        "comunicado_id",
        "partner_id",
        string="Además, a estos contactos",
    )
    destinatario_ids = fields.One2many(
        "gts.chatroom.comunicado.destinatario", "comunicado_id", "Destinatarios"
    )
    state = fields.Selection(
        [
            ("borrador", "Borrador"),
            ("enviando", "Enviando"),
            ("enviado", "Enviado"),
            ("error", "No salió"),
        ],
        "Estado",
        default="borrador",
        required=True,
        tracking=True,
        copy=False,
    )
    error = fields.Text("Por qué no salió", copy=False)
    enviado_el = fields.Datetime("Enviado el", copy=False, readonly=True)
    enviado_por_id = fields.Many2one(
        "res.users", "Enviado por", copy=False, readonly=True
    )
    total = fields.Integer("Destinatarios", compute="_compute_cuentas")
    aceptados = fields.Integer("Aceptados", compute="_compute_cuentas")
    rechazados = fields.Integer("Rechazados", compute="_compute_cuentas")
    entregados = fields.Integer("Entregados", compute="_compute_cuentas")
    leidos = fields.Integer("Leídos", compute="_compute_cuentas")
    fallidos = fields.Integer("Fallidos", compute="_compute_cuentas")

    @api.depends("plantilla_id.categoria")
    def _compute_es_marketing(self):
        for comunicado in self:
            comunicado.es_marketing = comunicado.plantilla_id.categoria == "MARKETING"

    def _lista_parametros(self):
        self.ensure_one()
        return [
            linea.strip() for linea in (self.parametros or "").splitlines() if linea
        ]

    @api.depends("plantilla_id.cuerpo", "parametros")
    def _compute_vista_previa(self):
        for comunicado in self:
            texto = comunicado.plantilla_id.cuerpo or ""
            for n, valor in enumerate(comunicado._lista_parametros(), start=1):
                texto = texto.replace(f"{{{{{n}}}}}", valor)
            comunicado.vista_previa = texto or False

    @api.depends("destinatario_ids.estado")
    def _compute_cuentas(self):
        for comunicado in self:
            estados = comunicado.destinatario_ids.mapped("estado")
            comunicado.total = len(estados)
            comunicado.rechazados = sum(
                e in ("rechazado", "sin_telefono") for e in estados
            )
            comunicado.aceptados = sum(e in AVANCE or e == "fallido" for e in estados)
            comunicado.entregados = sum(e in ("entregado", "leido") for e in estados)
            comunicado.leidos = estados.count("leido")
            comunicado.fallidos = estados.count("fallido")

    # ------------------------------------------------------------------
    # Preparar
    # ------------------------------------------------------------------

    def _comprobar_parametros(self):
        self.ensure_one()
        valores = self._lista_parametros()
        if len(valores) != self.variables:
            raise UserError(
                self.env._(
                    "La plantilla %(p)s necesita %(n)s datos, uno por línea, y "
                    "hay %(h)s.",
                    p=self.plantilla_id.name,
                    n=self.variables,
                    h=len(valores),
                )
            )
        for valor in valores:
            # Meta rechaza un dato con tabuladores o con mas de 4 espacios
            # seguidos, igual que con saltos de linea.
            if "\t" in valor or re.search(r" {5,}", valor):
                raise UserError(
                    self.env._(
                        "El dato «%s» lleva tabuladores o más de cuatro espacios "
                        "seguidos: WhatsApp no lo acepta.",
                        valor,
                    )
                )

    def accion_preparar(self):
        """Rehace la lista de destinatarios desde las listas y los contactos
        sueltos. Un mismo teléfono va una sola vez."""
        for comunicado in self:
            if comunicado.state != "borrador":
                raise UserError(
                    self.env._("Un comunicado enviado ya no se prepara de nuevo.")
                )
            comunicado.destinatario_ids.unlink()
            vistos = set()
            lineas = []
            for partner in comunicado.lista_ids.partner_ids | comunicado.partner_ids:
                telefono = _digitos(partner.phone_sanitized)
                if telefono and telefono in vistos:
                    continue
                vistos.add(telefono)
                lineas.append(
                    {
                        "comunicado_id": comunicado.id,
                        "partner_id": partner.id,
                        "telefono": telefono or False,
                        "estado": "pendiente" if telefono else "sin_telefono",
                    }
                )
            self.env["gts.chatroom.comunicado.destinatario"].create(lineas)
        return True

    # ------------------------------------------------------------------
    # Enviar
    # ------------------------------------------------------------------

    def accion_enviar(self):
        self.ensure_one()
        if self.state != "borrador":
            raise UserError(self.env._("Este comunicado ya se ha enviado."))
        if not self.plantilla_id.se_puede_enviar:
            raise UserError(
                self.plantilla_id.por_que_no
                or self.env._("Esta plantilla no se puede enviar.")
            )
        if self.gateway_id.integrated_webhook_state != "integrated":
            raise UserError(self.env._("La pasarela no está integrada."))
        self._comprobar_parametros()
        if not self.destinatario_ids:
            self.accion_preparar()
        if not self.destinatario_ids.filtered(lambda d: d.estado == "pendiente"):
            raise UserError(self.env._("Ningún destinatario tiene un teléfono válido."))
        self.write(
            {
                "state": "enviando",
                "error": False,
                "enviado_el": fields.Datetime.now(),
                "enviado_por_id": self.env.uid,
            }
        )
        comunicado_id = self.id
        registro = self.env.registry
        uid = self.env.uid

        @self.env.cr.postcommit.add
        def _enviar_tras_commit():
            with registro.cursor() as cr:
                env = api.Environment(cr, uid, {})
                env["gts.chatroom.comunicado"].browse(comunicado_id)._enviar_lotes()

        return True

    def _enviar_lotes(self):
        """Ya guardado: un POST por cada 300. Si el primero se rechaza entero
        (saldo, cuenta cortada) no sale nada y se dice por qué."""
        self.ensure_one()
        Alma = self.env["mail.gateway.alma"]
        pendientes = self.destinatario_ids.filtered(lambda d: d.estado == "pendiente")
        parametros = self._lista_parametros()
        errores = []
        for inicio in range(0, len(pendientes), POR_LOTE):
            trozo = pendientes[inicio : inicio + POR_LOTE]
            cuerpo = {
                "numero": _digitos(self.gateway_id.alma_numero),
                "plantilla": {"id": self.plantilla_id.alma_id},
                "destinatarios": [
                    {"a": d.telefono, "parametros": parametros} for d in trozo
                ],
            }
            try:
                respuesta = Alma._alma_peticion(
                    self.gateway_id,
                    "POST",
                    "/api/odoo/enviar/lote",
                    json=cuerpo,
                    headers={"Idempotency-Key": f"comunicado-{self.id}-{inicio}"},
                    timeout=TIMEOUT_LOTE,
                )
            except Exception as error:  # noqa: BLE001 - se ensena, no se pierde
                _logger.exception("ChatRoom: el lote del comunicado %s", self.id)
                errores.append(str(error))
                break
            datos = Alma._alma_json(respuesta)
            if not Alma._alma_ok(respuesta, datos):
                errores.append(Alma._alma_texto_error(respuesta, datos))
                break
            self._guardar_respuesta(trozo, datos)
        vals = {"state": "enviado" if not errores else "error"}
        if errores:
            vals["error"] = "\n".join(errores)
            if self.destinatario_ids.filtered(lambda d: d.estado in AVANCE):
                # Salio una parte: se dice, no se esconde detras de «error».
                vals["error"] += "\n\n" + self.env._(
                    "Una parte sí salió: mira los destinatarios."
                )
        self.write(vals)

    def _guardar_respuesta(self, trozo, datos):
        por_telefono = {d.telefono: d for d in trozo}
        Alma = self.env["mail.gateway.alma"]
        for aceptado in datos.get("aceptados") or []:
            linea = por_telefono.get(_digitos(aceptado.get("a")))
            if linea:
                linea.write(
                    {
                        "estado": "en_cola",
                        "mensaje_id": str(aceptado.get("mensaje_id") or "") or False,
                    }
                )
        for rechazado in datos.get("rechazados") or []:
            linea = por_telefono.get(_digitos(rechazado.get("a")))
            if linea:
                linea.write(
                    {
                        "estado": "rechazado",
                        "motivo": rechazado.get("alma_motivo") or False,
                        "motivo_texto": Alma._alma_texto_motivo(
                            rechazado.get("alma_motivo"), rechazado.get("error")
                        ),
                    }
                )

    def accion_volver_a_borrador(self):
        """Solo un comunicado que no salió: el que salió ya lo tiene la gente."""
        for comunicado in self:
            if comunicado.state != "error" or comunicado.destinatario_ids.filtered(
                lambda d: d.estado in AVANCE
            ):
                raise UserError(
                    self.env._(
                        "Solo vuelve a borrador un comunicado que no llegó a "
                        "salir a nadie."
                    )
                )
            comunicado.write({"state": "borrador", "error": False})
        return True


class GtsChatroomComunicadoDestinatario(models.Model):
    _name = "gts.chatroom.comunicado.destinatario"
    _description = "Destinatario de un comunicado"
    _order = "comunicado_id, id"

    comunicado_id = fields.Many2one(
        "gts.chatroom.comunicado",
        "Comunicado",
        required=True,
        ondelete="cascade",
        index=True,
    )
    partner_id = fields.Many2one("res.partner", "Contacto", ondelete="set null")
    telefono = fields.Char("Teléfono")
    estado = fields.Selection(
        ESTADOS_DESTINATARIO, "Estado", default="pendiente", required=True
    )
    motivo = fields.Char("Motivo (código)")
    motivo_texto = fields.Text("Motivo")
    mensaje_id = fields.Char("Id del mensaje en el portal", index=True)

    def _alma_avanzar(self, estado, motivo=False, frase=False):
        """Igual que el de un mensaje: solo hacia delante, que los acuses no
        llegan en orden."""
        for linea in self:
            actual = linea.estado
            if estado == "fallido":
                if actual in ("entregado", "leido", "fallido"):
                    continue
                linea.write(
                    {
                        "estado": "fallido",
                        "motivo": motivo or False,
                        "motivo_texto": self.env[
                            "mail.gateway.alma"
                        ]._alma_texto_motivo(motivo, frase),
                    }
                )
            elif estado in AVANCE and (
                actual not in AVANCE or AVANCE.index(estado) > AVANCE.index(actual)
            ):
                if actual == "fallido":
                    continue
                linea.estado = estado
