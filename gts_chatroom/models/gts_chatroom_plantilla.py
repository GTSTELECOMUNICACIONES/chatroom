"""LAS PLANTILLAS DE WHATSAPP, COPIADAS DEL PORTAL (25-11-2026).

Odoo no las crea ni las aprueba todavia: las lee de `GET /api/odoo/plantillas`
(docs/contrato-plantillas-y-comunicados.md). El portal tampoco llama al
proveedor en esa ruta -Gupshup da 10 peticiones por minuto-, asi que leerla no
gasta nada, pero tampoco se pide en cada desplegable: se copia con un boton.

Tres reglas de Carlos que aqui se respetan:
- se pinta `categoria` (la que Meta APLICA), no la declarada;
- un estado que no conocemos se ensena tal cual: Meta los anade sin avisar;
- `se_puede_enviar` manda: una plantilla con variable en cabecera o en boton
  se ve, pero no se puede elegir en un comunicado.
"""

import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from .mail_gateway_alma import _digitos, _fecha


def _mismo_texto(uno, otro):
    """Iguales salvo espacios y saltos de línea de sobra, que Meta a veces
    normaliza."""
    return " ".join((uno or "").split()) == " ".join((otro or "").split())


_logger = logging.getLogger(__name__)

# Los estados de Meta en el aviso, como los pinta el catálogo del portal.
ESTADOS_META = {
    "approved": "aprobada",
    "rejected": "rechazada",
    "pending": "pendiente",
    "paused": "pausada",
    "disabled": "desactivada",
}

CATEGORIAS = {
    "MARKETING": "Marketing",
    "UTILITY": "Utilidad",
    "AUTHENTICATION": "Autenticación",
}


class GtsChatroomPlantilla(models.Model):
    _name = "gts.chatroom.plantilla"
    _description = "Plantilla de WhatsApp"
    _order = "name"

    gateway_id = fields.Many2one(
        "mail.gateway", "Pasarela", required=True, ondelete="cascade", index=True
    )
    # Vacío mientras es un borrador creado en Odoo y no se ha mandado a
    # aprobar (gts_chatroom_plantilla_alta.py).
    alma_id = fields.Char("Id en el portal", index=True, copy=False)
    name = fields.Char("Nombre", required=True)
    idioma = fields.Char("Idioma")
    categoria = fields.Char(
        "Categoría (código)",
        help="La que aplica Meta, que es la que se cobra y la que decide si le "
        "llega a quien se dio de baja de publicidad.",
    )
    categoria_texto = fields.Char("Categoría", compute="_compute_categoria_texto")
    reclasificada = fields.Boolean(
        "Reclasificada por Meta",
        help="Se dio de alta con otra categoría y Meta la cobra como esta.",
    )
    estado = fields.Char(
        "Estado en Meta",
        help="Aprobada, pendiente, rechazada, pausada… Un estado nuevo de Meta "
        "sale tal cual.",
    )
    motivo = fields.Text("Motivo del rechazo")
    cuerpo = fields.Text("Texto")
    cabecera = fields.Char("Cabecera")
    variables_cuerpo = fields.Integer(
        "Datos que hay que rellenar",
        help="Cuántos {{1}}, {{2}}… lleva el texto.",
    )
    # LOS EJEMPLOS QUE VIO META (Carlos, 25-11-2026). Meta guarda el cuerpo
    # entero relleno; el portal lo separa por dato cuando puede. Con dos
    # datos pegados no se puede, y entonces solo viene el texto entero:
    # mejor ninguna pista por casilla que una equivocada.
    ejemplos = fields.Json("Ejemplos por dato")
    ejemplo_texto = fields.Text("Ejemplo que vio Meta")
    se_puede_enviar = fields.Boolean("Se puede enviar")
    por_que_no = fields.Char("Por qué no se puede enviar")
    # EL CUPO DE CAMBIOS DE META (Carlos, 26-11-2026), del GET de plantillas:
    # 10 cada 30 días y 1 cada 24 h, SOLO en las aprobadas. `ediciones: null`
    # no es cero: no aplica (rechazada, pausada) o no se pudo contar, y
    # entonces no se enseña nada. `proxima_desde: null` es «ya».
    ediciones_cuentan = fields.Boolean(readonly=True)
    ediciones_quedan = fields.Integer("Cambios que quedan este mes", readonly=True)
    ediciones_proxima = fields.Datetime("Próximo cambio desde", readonly=True)
    active = fields.Boolean(default=True)

    _alma_id_unico = models.Constraint(
        "UNIQUE(gateway_id, alma_id)", "Esa plantilla ya está copiada."
    )

    @api.depends("categoria")
    def _compute_categoria_texto(self):
        for plantilla in self:
            plantilla.categoria_texto = CATEGORIAS.get(
                plantilla.categoria, plantilla.categoria or ""
            )

    @api.depends("name", "categoria", "variables_cuerpo")
    def _compute_display_name(self):
        for plantilla in self:
            if not plantilla.name:
                plantilla.display_name = self.env._("Nueva plantilla")
                continue
            plantilla.display_name = (
                f"{plantilla.name} ({plantilla.categoria_texto or '?'})"
            )

    # SIN @api.model: el botón de la cabecera de la lista llama con los ids
    # marcados (vacíos) como argumento, y un método de modelo revienta con
    # «takes 1 positional argument but 2 were given» (25-11-2026).
    def accion_actualizar(self):
        """Botón de la lista: copia el catálogo de todas las pasarelas del
        portal integradas. No usa los registros: copia todo."""
        pasarelas = self.env["mail.gateway"].search(
            [
                ("gateway_type", "=", "alma"),
                ("integrated_webhook_state", "=", "integrated"),
            ]
        )
        if not pasarelas:
            raise UserError(
                self.env._("No hay ninguna pasarela del portal de Alma integrada.")
            )
        avisos = []
        total = 0
        for gateway in pasarelas:
            copiadas, aviso = self._copiar_del_portal(gateway)
            total += copiadas
            if aviso:
                avisos.append(f"{gateway.name}: {aviso}")
        mensaje = self.env._("%s plantillas copiadas del portal.", total)
        if avisos:
            mensaje += "\n" + "\n".join(avisos)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Plantillas"),
                "type": "warning" if avisos else "success",
                "message": mensaje,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    @api.model
    def _gts_aviso_de_meta(self, gateway, datos):
        """EL AVISO DE META AL MOMENTO (Carlos, 26-11-2026): el portal manda
        {tipo: plantilla, plantilla: {id, nombre, estado, categoria,
        categoria_declarada, motivo}} cuando Meta aprueba, rechaza o
        reclasifica. Se aplica lo que trae y se pide el catálogo entero
        enseguida, que es el que dice si `se_puede_enviar`. El cron de 10
        minutos queda de red: el aviso es rápido y se puede perder."""
        if not isinstance(datos, dict) or not datos.get("id"):
            return
        plantilla = self.with_context(active_test=False).search(
            [("gateway_id", "=", gateway.id), ("alma_id", "=", str(datos["id"]))],
            limit=1,
        )
        if plantilla:
            vals = {}
            if datos.get("estado"):
                estado = str(datos["estado"]).lower()
                vals["estado"] = ESTADOS_META.get(estado, estado)
                if estado != "approved":
                    vals["se_puede_enviar"] = False
            if datos.get("categoria"):
                vals["categoria"] = datos["categoria"]
                vals["reclasificada"] = bool(
                    datos.get("categoria_declarada")
                    and datos["categoria_declarada"] != datos["categoria"]
                )
            if "motivo" in datos:
                vals["motivo"] = datos.get("motivo") or False
            plantilla.write(vals)
        cron = self.env.ref(
            "gts_chatroom.ir_cron_sincronizar_plantillas", raise_if_not_found=False
        )
        if cron:
            cron.sudo()._trigger()

    @api.model
    def _gts_sincronizar_plantillas(self):
        """EL CHATROOM SE MANEJA ENTERO DESDE ODOO (David, 26-11-2026): nadie
        tiene que pulsar «copiar del portal» para ver si Meta aprobó una
        plantilla. Lo hace el cron cada 10 minutos y, tras mandar una a
        aprobar, a los 2. Una pasarela que falla no para a las demás."""
        pasarelas = (
            self.env["mail.gateway"]
            .sudo()
            .search(
                [
                    ("gateway_type", "=", "alma"),
                    ("integrated_webhook_state", "=", "integrated"),
                ]
            )
        )
        for gateway in pasarelas:
            try:
                with self.env.cr.savepoint():
                    self.sudo()._copiar_del_portal(gateway)
            except Exception:  # noqa: BLE001 - se apunta y se sigue
                _logger.warning(
                    "ChatRoom: no se pudieron sincronizar las plantillas de %s",
                    gateway.id,
                    exc_info=True,
                )
        return True

    @api.model
    def _gts_valores_ediciones(self, ediciones):
        if not isinstance(ediciones, dict):
            return {
                "ediciones_cuentan": False,
                "ediciones_quedan": 0,
                "ediciones_proxima": False,
            }
        proxima = ediciones.get("proxima_desde")
        return {
            "ediciones_cuentan": True,
            "ediciones_quedan": int(ediciones.get("quedan_30d") or 0),
            "ediciones_proxima": _fecha(proxima) if proxima else False,
        }

    @api.model
    def _copiar_del_portal(self, gateway):
        """Devuelve (cuántas, aviso del portal). Las que el portal ya no
        devuelve se archivan, no se borran: un comunicado viejo la cita."""
        Alma = self.env["mail.gateway.alma"]
        respuesta = Alma._alma_peticion(
            gateway,
            "GET",
            "/api/odoo/plantillas",
            params={"numero": _digitos(gateway.alma_numero)},
        )
        datos = Alma._alma_json(respuesta)
        if not Alma._alma_ok(respuesta, datos) or not isinstance(
            datos.get("plantillas", []), list
        ):
            raise UserError(Alma._alma_texto_error(respuesta, datos))
        Plantilla = self.with_context(active_test=False)
        vistas = self.browse()
        # Lo que solo sabe Odoo de una plantilla creada aquí (su nombre
        # legible, a qué se aplica, de dónde sale cada dato) no se pisa.
        for item in datos.get("plantillas") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            variables = item.get("variables") or {}
            cabecera = item.get("cabecera") or {}
            vals = {
                "gateway_id": gateway.id,
                "alma_id": str(item["id"]),
                "name": item.get("nombre") or str(item["id"]),
                "idioma": item.get("idioma") or False,
                "categoria": item.get("categoria") or False,
                "reclasificada": bool(
                    item.get("categoria_declarada")
                    and item.get("categoria")
                    and item["categoria_declarada"] != item["categoria"]
                ),
                "estado": item.get("estado") or False,
                "motivo": item.get("motivo") or False,
                "cuerpo": item.get("cuerpo") or False,
                "cabecera": (
                    cabecera.get("texto") or cabecera.get("tipo")
                    if isinstance(cabecera, dict)
                    else False
                )
                or False,
                "variables_cuerpo": int(variables.get("cuerpo") or 0),
                "ejemplos": (
                    item["ejemplos"]
                    if isinstance(item.get("ejemplos"), dict)
                    else False
                ),
                "ejemplo_texto": item.get("ejemplo_texto") or False,
                "se_puede_enviar": bool(item.get("se_puede_enviar")),
                "por_que_no": item.get("por_que_no") or False,
                **self._gts_valores_ediciones(item.get("ediciones")),
                "active": True,
            }
            plantilla = Plantilla.search(
                [("gateway_id", "=", gateway.id), ("alma_id", "=", vals["alma_id"])],
                limit=1,
            )
            if plantilla.en_modificacion:
                # Alguien la está modificando aquí: el texto del portal es el
                # viejo y no puede pisar lo que se está escribiendo, ni
                # volver a dejarla para enviar.
                for clave in (
                    "name",
                    "cuerpo",
                    "cabecera",
                    "variables_cuerpo",
                    "ejemplos",
                    "se_puede_enviar",
                    "por_que_no",
                ):
                    vals.pop(clave, None)
            if not plantilla:
                # UN BORRADOR QUE YA SE PRESENTÓ y cuya respuesta no llegó
                # (corte, tiempo agotado): está en Meta y aquí sin id. Se le
                # pone el suyo por el nombre en vez de crear otra (Carlos,
                # 26-11-2026).
                # SOLO SI EL TEXTO ES EL MISMO: con el nombre a secas, un
                # borrador adoptaría una plantilla AJENA que ya usaba ese
                # nombre y saldría otro texto al cliente sin ningún error
                # (Carlos). Si el texto no casa, el nombre está ocupado y se
                # le dice al borrador.
                mismo_nombre = Plantilla.search(
                    [
                        ("gateway_id", "=", gateway.id),
                        ("alma_id", "=", False),
                        ("creada_en_odoo", "=", True),
                    ]
                ).filtered(lambda p, n=vals["name"]: p.nombre_meta == n)
                plantilla = mismo_nombre.filtered(
                    lambda p, c=vals["cuerpo"]: _mismo_texto(p.cuerpo, c)
                )[:1]
                (mismo_nombre - plantilla).write(
                    {
                        "motivo": self.env._(
                            "Ese nombre ya lo usa en Meta otra plantilla con otro "
                            "texto. Cámbiale el nombre antes de enviarla."
                        )
                    }
                )
            if plantilla:
                if plantilla.creada_en_odoo:
                    vals.pop("name")
                plantilla.write(vals)
            else:
                plantilla = Plantilla.create(vals)
            vistas |= plantilla
        # Los borradores de Odoo no están en el portal todavía: no se archivan.
        Plantilla.search(
            [
                ("gateway_id", "=", gateway.id),
                ("id", "not in", vistas.ids),
                ("alma_id", "!=", False),
            ]
        ).write({"active": False})
        return len(vistas), datos.get("aviso") or False
