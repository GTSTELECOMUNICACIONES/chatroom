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

from odoo import api, fields, models
from odoo.exceptions import UserError

from .mail_gateway_alma import _digitos

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
                "active": True,
            }
            plantilla = Plantilla.search(
                [("gateway_id", "=", gateway.id), ("alma_id", "=", vals["alma_id"])],
                limit=1,
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
