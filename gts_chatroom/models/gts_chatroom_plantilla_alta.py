"""CREAR PLANTILLAS DESDE ODOO Y USARLAS DESDE CUALQUIER FICHA (David,
26-11-2026), como el módulo oficial de WhatsApp.

- Se escribe la plantilla aquí: categoría, idioma, cabecera, cuerpo con
  {{1}}, {{2}}…, pie, botones y UN EJEMPLO POR DATO (Meta los exige).
  «Enviar para aprobación» la manda al portal (`POST /api/odoo/plantillas`),
  que se la pasa a Meta. El estado vuelve al copiar del portal: el portal no
  avisa por webhook de los cambios de Meta.
- «Aplica a»: el modelo desde el que se manda (Contacto, Oportunidad,
  Pedido…). Cada dato puede salir de un campo de esa ficha («Nombre del
  contacto») o escribirse al enviar. En esos modelos aparece «Enviar
  WhatsApp» en el menú Acción.

Las reglas de Meta que se comprueban ANTES de mandar, para que el rechazo
no tarde un día: los datos van seguidos desde {{1}}, el texto no empieza ni
acaba con un dato, cada dato tiene ejemplo y los largos máximos.
"""

import re
import unicodedata
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .mail_gateway_alma import _digitos

DATO = re.compile(r"\{\{\s*(\d+)\s*\}\}")
# Un dato a medio escribir: «{{2]]», «{2}}», «{{2}», «[[2]]»… Se queda como
# texto y la plantilla sale con un dato de menos (David, 26-11-2026).
DATO_MAL = re.compile(r"[\{\[]{1,2}\s*\d+\s*[\}\]]{1,2}")
CATEGORIAS_ALTA = [("UTILITY", "Utilidad"), ("MARKETING", "Marketing")]
IDIOMAS = [
    ("es", "Español"),
    ("en", "Inglés"),
    ("ca", "Catalán"),
    ("pt_PT", "Portugués"),
]
# Los máximos de Meta.
MAX_CUERPO = 1024
MAX_CABECERA = 60
MAX_PIE = 60
MAX_BOTON = 25
MAX_BOTONES = 10
MAX_BOTONES_URL = 2
MAX_BOTONES_TELEFONO = 1
# Los tipos de botón de Meta, tal cual (Carlos, 26-11-2026: traducirlos
# sería otro sitio donde equivocarse).
TIPO_BOTON_META = {"respuesta": "QUICK_REPLY", "url": "URL", "telefono": "PHONE_NUMBER"}


def nombre_meta(nombre):
    """«Recordatorio de clase» → «recordatorio_de_clase»: Meta solo acepta
    minúsculas, números y guiones bajos."""
    texto = unicodedata.normalize("NFKD", nombre or "")
    texto = texto.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")[:512]


# La forma jurídica al final del nombre: «MARLON KART S.L», «Talleres SA».
FORMA_JURIDICA = re.compile(
    r"\b(S\.?\s?L\.?\s?U?|S\.?\s?A\.?\s?U?|S\.?\s?COOP|C\.?\s?B|S\.?\s?C|"
    r"LTD|GMBH|INC|LLC)\.?\s*$",
    re.IGNORECASE,
)


def solo_nombre_de_pila(registro, ruta, texto):
    """«Antonio Jiménez Arellano» → «Antonio» (David, 26-11-2026: «Hola
    Antonio Jiménez Arellano» no es como se le habla a nadie).

    Una EMPRESA se queda entera («Hola MARLON KART S.L», no «Hola MARLON»),
    pero se reconoce por la FORMA JURÍDICA del nombre, no por la casilla
    «Es una empresa»: en GTS Racing había personas marcadas como empresa y
    a Antonio le salía el nombre entero."""
    texto = (texto or "").strip()
    if FORMA_JURIDICA.search(texto):
        return texto
    return texto.split(" ")[0]


def valor_de_campo(registro, ruta):
    """El valor de `partner_id.name` en un registro, como texto. Un campo
    que ya no existe da vacío, no un error al enviar."""
    valor = registro
    try:
        for trozo in (ruta or "").split("."):
            valor = valor[:1][trozo] if isinstance(valor, models.BaseModel) else ""
    except (KeyError, AttributeError):
        return ""
    if isinstance(valor, models.BaseModel):
        return ", ".join(valor.mapped("display_name"))
    if valor is False or valor is None:
        return ""
    if isinstance(valor, float):
        return f"{valor:.2f}".replace(".", ",")
    return str(valor).strip()


class GtsChatroomPlantillaAlta(models.Model):
    _inherit = "gts.chatroom.plantilla"

    gateway_id = fields.Many2one(
        default=lambda self: self.env["res.partner"]._gts_pasarelas_alma()[:1]
    )
    # Por defecto SÍ: una ficha nueva en pantalla es un borrador de Odoo y
    # tiene que poder escribirse ANTES de guardar (el 26-11-2026 salía todo
    # bloqueado). Lo que copia el portal lo pone a False en `create`.
    creada_en_odoo = fields.Boolean(default=True, readonly=True, copy=False)
    editable = fields.Boolean(compute="_compute_editable")
    categoria_alta = fields.Selection(
        CATEGORIAS_ALTA,
        "Categoría",
        default="UTILITY",
        help="Utilidad: avisos de algo que el cliente ya tiene (clase, pedido, "
        "cita). Marketing: promociones; no llega a quien se dio de baja. Meta "
        "puede reclasificarla y se cobra como la que aplique.",
    )
    idioma_alta = fields.Selection(IDIOMAS, "Idioma", default="es")
    pie = fields.Char("Pie")
    boton_ids = fields.One2many(
        "gts.chatroom.plantilla.boton", "plantilla_id", "Botones", copy=True
    )
    variable_ids = fields.One2many(
        "gts.chatroom.plantilla.variable", "plantilla_id", "Datos", copy=True
    )
    model_id = fields.Many2one(
        "ir.model",
        "Aplica a",
        ondelete="set null",
        # Las fichas de negocio con historial (contactos, oportunidades,
        # pedidos…), no las tablas internas del propio ChatRoom.
        domain=[
            ("transient", "=", False),
            ("is_mail_thread", "=", True),
            ("model", "not like", "gts.chatroom"),
            ("model", "not in", ["discuss.channel", "mail.gateway"]),
        ],
        default=lambda self: self.env.ref("base.model_res_partner"),
        help="Desde qué fichas se puede mandar. Sus campos sirven para "
        "rellenar los datos.",
    )
    modelo = fields.Char(related="model_id.model")
    campo_telefono = fields.Char(
        "Campo del teléfono",
        default="phone",
        help="De dónde sale el número en esa ficha, por ejemplo «phone» o "
        "«partner_id.phone».",
    )
    datos_mal_escritos = fields.Char(compute="_compute_datos_mal_escritos")
    asi_lo_ve_meta = fields.Text("Así lo ve Meta", compute="_compute_asi_lo_ve_meta")
    aviso_ejemplos = fields.Char(compute="_compute_asi_lo_ve_meta")
    nombre_meta = fields.Char(
        "Nombre para Meta",
        compute="_compute_nombre_meta",
        help="Minúsculas y guiones bajos: así lo pide Meta.",
    )

    @api.depends("creada_en_odoo", "alma_id")
    def _compute_editable(self):
        # Una plantilla ya en Meta NO se edita, ni rechazada: Meta no lo deja
        # y tampoco deja reusar su nombre (Carlos, 26-11-2026). Se duplica,
        # que crea un borrador con otro nombre.
        for plantilla in self:
            plantilla.editable = plantilla.creada_en_odoo and not plantilla.alma_id

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        for plantilla, vals in zip(self, vals_list, strict=True):
            if "name" not in (default or {}):
                vals["name"] = plantilla._siguiente_version()
        return vals_list

    def _siguiente_version(self):
        """«aviso_clases» → «aviso_clases_v2», o _v3 si la v2 ya existe. Un
        sufijo fijo y no texto libre: Meta solo admite minúsculas, números y
        guion bajo, y no deja reusar un nombre (Carlos, 26-11-2026)."""
        self.ensure_one()
        base = re.sub(r"_v\d+$", "", nombre_meta(self.name))
        usados = set(
            self.with_context(active_test=False)
            .search([("gateway_id", "=", self.gateway_id.id)])
            .mapped("nombre_meta")
        )
        n = 2
        while f"{base}_v{n}" in usados:
            n += 1
        return f"{base}_v{n}"

    @api.depends("cuerpo")
    def _compute_datos_mal_escritos(self):
        for plantilla in self:
            malos = [
                m.group(0)
                for m in DATO_MAL.finditer(plantilla.cuerpo or "")
                if not DATO.fullmatch(m.group(0))
            ]
            plantilla.datos_mal_escritos = ", ".join(malos) or False

    @api.depends("cuerpo", "variable_ids.ejemplo", "variable_ids.posicion")
    def _compute_asi_lo_ve_meta(self):
        """EL REVISOR DE META NO LEE LA PLANTILLA, LEE EL EJEMPLO MONTADO
        (Carlos, 26-11-2026). «Te escribo para {{2}}» con el ejemplo «Te
        escribo para recordarte…» queda duplicado y cortado: parece un uso
        falso y se rechaza. Y ese ejemplo es luego la pista de la casilla."""
        for plantilla in self:
            texto = plantilla.cuerpo or ""
            avisos = []
            for variable in plantilla.variable_ids:
                ejemplo = (variable.ejemplo or "").strip()
                marca = re.compile(rf"\{{\{{\s*{variable.posicion}\s*\}}\}}")
                encontrado = marca.search(texto)
                if ejemplo and encontrado:
                    antes = texto[: encontrado.start()].split()
                    # Las 1-3 palabras justo antes del hueco, repetidas al
                    # principio del ejemplo.
                    for n in (3, 2, 1):
                        trozo = " ".join(antes[-n:]).lower().strip(",.:;")
                        if (
                            len(antes) >= n
                            and trozo
                            and ejemplo.lower().startswith(trozo)
                        ):
                            avisos.append(
                                self.env._(
                                    "el ejemplo %(n)s repite «%(t)s», que ya está "
                                    "en el texto",
                                    n=variable.posicion,
                                    t=" ".join(antes[-n:]),
                                )
                            )
                            break
                if ejemplo.endswith(("…", "...")):
                    avisos.append(
                        self.env._(
                            "el ejemplo %s acaba en «…»: escríbelo entero",
                            variable.posicion,
                        )
                    )
                if ejemplo:
                    # Con función: un «\1» en el ejemplo no es una referencia.
                    texto = marca.sub(lambda _m, e=ejemplo: e, texto)
            plantilla.asi_lo_ve_meta = texto or False
            plantilla.aviso_ejemplos = "; ".join(avisos) or False

    def accion_anadir_variable(self):
        """«+ Dato», como el botón «+ variable» del WhatsApp oficial: pone
        el siguiente {{n}} al final del texto. Luego se mueve donde vaya."""
        for plantilla in self.filtered("editable"):
            numeros = [int(n) for n in DATO.findall(plantilla.cuerpo or "")]
            siguiente = max(numeros, default=0) + 1
            cuerpo = (plantilla.cuerpo or "").rstrip()
            plantilla.cuerpo = f"{cuerpo} {{{{{siguiente}}}}}".lstrip()
        return True

    @api.depends("name")
    def _compute_nombre_meta(self):
        for plantilla in self:
            plantilla.nombre_meta = nombre_meta(plantilla.name)

    # ------------------------------------------------------------------
    # Una casilla por dato, al escribir el texto
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("alma_id"):
                # Sin id del portal solo se crea desde aquí (también al
                # duplicar una rechazada para corregirla).
                vals["creada_en_odoo"] = True
                vals.update(estado="borrador", motivo=False, se_puede_enviar=False)
            else:
                vals.setdefault("creada_en_odoo", False)
            self._valores_de_alta(vals)
        plantillas = super().create(vals_list)
        plantillas._ajustar_variables()
        if plantillas.filtered("model_id"):
            self._gts_sincronizar_acciones()
        return plantillas

    def write(self, vals):
        self._valores_de_alta(vals)
        res = super().write(vals)
        if {"cuerpo", "variables_cuerpo", "ejemplos"} & set(vals):
            self._ajustar_variables()
        if {"model_id", "active"} & set(vals):
            self._gts_sincronizar_acciones()
        return res

    def unlink(self):
        res = super().unlink()
        self._gts_sincronizar_acciones()
        return res

    @api.model
    def _valores_de_alta(self, vals):
        """La categoría y el idioma de alta son los de la plantilla; y los
        datos se cuentan en el texto."""
        if vals.get("categoria_alta"):
            vals["categoria"] = vals["categoria_alta"]
        if vals.get("idioma_alta"):
            vals["idioma"] = vals["idioma_alta"]
        if "cuerpo" in vals and "variables_cuerpo" not in vals:
            vals["variables_cuerpo"] = len(set(DATO.findall(vals["cuerpo"] or "")))

    @api.onchange("cuerpo")
    def _onchange_cuerpo(self):
        self.variables_cuerpo = len(set(DATO.findall(self.cuerpo or "")))
        self._ajustar_variables()

    def _ajustar_variables(self):
        """Una línea por dato, del 1 al N, conservando lo ya puesto. El
        ejemplo de una plantilla del portal sale de sus ejemplos."""
        for plantilla in self:
            actuales = {v.posicion: v for v in plantilla.variable_ids}
            total = plantilla.variables_cuerpo or 0
            ejemplos = (
                plantilla.ejemplos if isinstance(plantilla.ejemplos, dict) else {}
            )
            comandos = [
                (2, v.id) if isinstance(v.id, int) else (3, v.id)
                for n, v in actuales.items()
                if n > total
            ]
            for n in range(1, total + 1):
                if n in actuales:
                    if not actuales[n].ejemplo and ejemplos.get(str(n)):
                        comandos.append(
                            (1, actuales[n].id, {"ejemplo": ejemplos[str(n)]})
                        )
                    continue
                comandos.append(
                    (0, 0, {"posicion": n, "ejemplo": ejemplos.get(str(n)) or False})
                )
            if comandos:
                plantilla.variable_ids = comandos

    @api.model
    def _gts_al_actualizar(self):
        """Al actualizar el módulo: las casillas de datos de las plantillas
        que ya estaban copiadas y las acciones «Enviar WhatsApp»."""
        self.with_context(active_test=False).search([])._ajustar_variables()
        self._gts_sincronizar_acciones()
        return True

    # ------------------------------------------------------------------
    # Enviar para aprobación
    # ------------------------------------------------------------------

    def _comprobar_para_meta(self):
        """Lo que Meta rechaza, dicho aquí y en castellano."""
        self.ensure_one()
        errores = []
        cuerpo = (self.cuerpo or "").strip()
        if not self.name or not self.nombre_meta:
            errores.append(self.env._("Ponle un nombre."))
        if self.datos_mal_escritos:
            errores.append(
                self.env._(
                    "Dato mal escrito: %s. Se escribe con dos llaves, {{2}}.",
                    self.datos_mal_escritos,
                )
            )
        if not cuerpo:
            errores.append(self.env._("Escribe el texto."))
        if len(cuerpo) > MAX_CUERPO:
            errores.append(self.env._("El texto pasa de %s caracteres.", MAX_CUERPO))
        numeros = sorted({int(n) for n in DATO.findall(cuerpo)})
        if numeros and numeros != list(range(1, len(numeros) + 1)):
            errores.append(
                self.env._("Los datos tienen que ir seguidos: {{1}}, {{2}}, {{3}}…")
            )
        if DATO.match(cuerpo) or re.search(r"\{\{\s*\d+\s*\}\}[\s.!?]*$", cuerpo):
            errores.append(
                self.env._(
                    "Meta no acepta un texto que empiece o acabe con un dato: "
                    "pon alguna palabra antes y después."
                )
            )
        sin_ejemplo = self.variable_ids.filtered(
            lambda v: not (v.ejemplo or "").strip()
        )
        if sin_ejemplo:
            errores.append(
                self.env._(
                    "Falta el ejemplo de los datos %s: Meta los pide para aprobarla.",
                    ", ".join(str(p) for p in sin_ejemplo.mapped("posicion")),
                )
            )
        if self.cabecera and (
            len(self.cabecera) > MAX_CABECERA or DATO.search(self.cabecera)
        ):
            errores.append(
                self.env._(
                    "La cabecera es de hasta %s caracteres y sin datos.", MAX_CABECERA
                )
            )
        if self.pie and (len(self.pie) > MAX_PIE or DATO.search(self.pie)):
            errores.append(
                self.env._("El pie es de hasta %s caracteres y sin datos.", MAX_PIE)
            )
        tipos = self.boton_ids.mapped("tipo")
        if (
            len(tipos) > MAX_BOTONES
            or tipos.count("url") > MAX_BOTONES_URL
            or tipos.count("telefono") > MAX_BOTONES_TELEFONO
        ):
            errores.append(
                self.env._(
                    "Como mucho %(t)s botones: %(u)s de abrir una web y %(f)s "
                    "de llamar.",
                    t=MAX_BOTONES,
                    u=MAX_BOTONES_URL,
                    f=MAX_BOTONES_TELEFONO,
                )
            )
        for boton in self.boton_ids:
            if len(boton.texto or "") > MAX_BOTON:
                errores.append(
                    self.env._(
                        "El botón «%(b)s» pasa de %(n)s caracteres.",
                        b=boton.texto,
                        n=MAX_BOTON,
                    )
                )
            if boton.tipo != "respuesta" and not boton.valor:
                errores.append(
                    self.env._(
                        "Al botón «%s» le falta la dirección o el teléfono.",
                        boton.texto,
                    )
                )
            if DATO.search(boton.valor or ""):
                errores.append(
                    self.env._(
                        "El botón «%s» lleva un dato: el portal todavía no los envía.",
                        boton.texto,
                    )
                )
        if errores:
            raise UserError("\n".join(f"• {e}" for e in errores))

    def _cuerpo_para_el_portal(self):
        self.ensure_one()
        return {
            "numero": _digitos(self.gateway_id.alma_numero),
            "nombre": self.nombre_meta,
            "idioma": self.idioma or "es",
            "categoria": self.categoria or "UTILITY",
            "cuerpo": (self.cuerpo or "").strip(),
            "cabecera_tipo": "TEXT" if self.cabecera else "NONE",
            "cabecera_texto": self.cabecera or None,
            "pie": self.pie or None,
            "botones": [b._para_el_portal() for b in self.boton_ids],
            "ejemplos": {
                str(v.posicion): (v.ejemplo or "").strip()
                for v in self.variable_ids.sorted("posicion")
            },
        }

    def accion_enviar_aprobacion(self):
        self.ensure_one()
        if not self.editable:
            raise UserError(self.env._("Esta plantilla ya está en Meta."))
        if self.gateway_id.integrated_webhook_state != "integrated":
            raise UserError(
                self.env._("El canal %s no está integrado.", self.gateway_id.name)
            )
        self._comprobar_para_meta()
        Alma = self.env["mail.gateway.alma"]
        try:
            respuesta = Alma._alma_peticion(
                self.gateway_id.sudo(),
                "POST",
                "/api/odoo/plantillas",
                json=self._cuerpo_para_el_portal(),
            )
        except Exception as error:  # noqa: BLE001 - se explica, no se pierde
            # Una respuesta que no llega no quiere decir que no se presentara.
            raise UserError(
                self.env._(
                    "No llegó la respuesta del portal (%s). Puede que la "
                    "plantilla SÍ se presentara: espera unos minutos antes de "
                    "volver a enviarla. Si se presentó, este borrador se enlaza "
                    "con ella solo al actualizarse las plantillas.",
                    error,
                )
            ) from error
        datos = Alma._alma_json(respuesta)
        if not Alma._alma_ok(respuesta, datos) or not datos.get("id"):
            # El portal devuelve TODOS los fallos de golpe en `fallos`, y si
            # Meta la rechaza, su frase literal en `error` (Carlos).
            fallos = datos.get("fallos")
            if isinstance(fallos, list) and fallos:
                raise UserError(
                    self.env._("El portal no la acepta:")
                    + "\n"
                    + "\n".join(f"• {f}" for f in fallos)
                )
            raise UserError(Alma._alma_texto_error(respuesta, datos))
        self.write(
            {
                "alma_id": str(datos["id"]),
                "estado": datos.get("estado") or "pendiente",
                "motivo": False,
            }
        )
        # A los dos minutos se mira si Meta ya contestó; luego, cada diez.
        cron = self.env.ref(
            "gts_chatroom.ir_cron_sincronizar_plantillas", raise_if_not_found=False
        )
        if cron:
            cron.sudo()._trigger(at=fields.Datetime.now() + timedelta(minutes=2))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Enviada a Meta"),
                "type": "success",
                "message": self.env._(
                    "Meta suele contestar en minutos, a veces en un día. El "
                    "estado se actualiza solo: cuando la apruebe, aparecerá para "
                    "enviar."
                ),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    # ------------------------------------------------------------------
    # «Enviar WhatsApp» en el menú Acción de los modelos que la usan
    # ------------------------------------------------------------------

    @api.model
    def _gts_sincronizar_acciones(self):
        """Una acción «Enviar WhatsApp» por cada modelo al que se aplica
        alguna plantilla. Se rehacen enteras: son pocas."""
        Accion = self.env["ir.actions.act_window"].sudo()
        existentes = Accion.search(
            [
                ("res_model", "=", "gts.chatroom.nueva"),
                ("binding_model_id", "!=", False),
            ]
        )
        modelos = (
            self.sudo()
            .search([("model_id", "!=", False)])
            .mapped("model_id")
            .filtered(lambda m: m.model in self.env)
        )
        por_modelo = {a.binding_model_id: a for a in existentes}
        for modelo in modelos:
            if modelo in por_modelo:
                continue
            Accion.create(
                {
                    "name": self.env._("Enviar WhatsApp"),
                    "res_model": "gts.chatroom.nueva",
                    "view_mode": "form",
                    "target": "new",
                    "binding_model_id": modelo.id,
                    "binding_view_types": "form",
                    "context": "{'gts_desde_registro': True}",
                    "group_ids": [
                        (6, 0, self.env.ref("mail_gateway.gateway_user").ids)
                    ],
                }
            )
        existentes.filtered(lambda a: a.binding_model_id not in modelos).unlink()
        return True


class GtsChatroomPlantillaVariable(models.Model):
    _name = "gts.chatroom.plantilla.variable"
    _description = "Dato de una plantilla de WhatsApp"
    _order = "plantilla_id, posicion"

    plantilla_id = fields.Many2one(
        "gts.chatroom.plantilla", required=True, ondelete="cascade", index=True
    )
    posicion = fields.Integer("Dato", required=True)
    modelo = fields.Char(related="plantilla_id.model_id.model")

    @api.model_create_multi
    def create(self, vals_list):
        """RED: si una línea llega sin número (el cliente no manda un campo
        readonly sin `force_save`, y una pestaña vieja en caché no lo lleva),
        se numera por orden detrás de las que ya hay. Que guardar la
        plantilla no dependa de lo que pinte la pantalla (26-11-2026)."""
        siguiente = {}
        for vals in vals_list:
            if vals.get("posicion") or not vals.get("plantilla_id"):
                continue
            plantilla_id = vals["plantilla_id"]
            if plantilla_id not in siguiente:
                siguiente[plantilla_id] = (
                    max(
                        self.search([("plantilla_id", "=", plantilla_id)]).mapped(
                            "posicion"
                        ),
                        default=0,
                    )
                    + 1
                )
            usados = {
                v.get("posicion")
                for v in vals_list
                if v.get("plantilla_id") == plantilla_id
            }
            while siguiente[plantilla_id] in usados:
                siguiente[plantilla_id] += 1
            vals["posicion"] = siguiente[plantilla_id]
            siguiente[plantilla_id] += 1
        return super().create(vals_list)

    ejemplo = fields.Char(
        "Ejemplo para Meta",
        help="Escribe SOLO lo que va en ese hueco, no la frase entera: Meta lee "
        "el texto con los ejemplos puestos, y una frase repetida o cortada con "
        "«…» es motivo de rechazo.",
    )
    origen = fields.Selection(
        [("libre", "Se escribe al enviar"), ("campo", "Un campo de la ficha")],
        "De dónde sale",
        default="libre",
        required=True,
    )
    campo = fields.Char("Campo", help="Por ejemplo «name» o «partner_id.name».")
    por_defecto = fields.Char("Texto por defecto")
    formato = fields.Selection(
        [("tal_cual", "Tal cual"), ("nombre_pila", "Solo el nombre de pila")],
        "Cómo se pone",
        default="tal_cual",
        required=True,
        help="«Solo el nombre de pila»: «Antonio» en vez de «Antonio Jiménez "
        "Arellano». A una empresa se le pone el nombre entero.",
    )


class GtsChatroomPlantillaBoton(models.Model):
    _name = "gts.chatroom.plantilla.boton"
    _description = "Botón de una plantilla de WhatsApp"
    _order = "plantilla_id, sequence, id"

    plantilla_id = fields.Many2one(
        "gts.chatroom.plantilla", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    tipo = fields.Selection(
        [
            ("respuesta", "Respuesta rápida"),
            ("url", "Abrir una web"),
            ("telefono", "Llamar"),
        ],
        "Tipo",
        default="respuesta",
        required=True,
    )
    texto = fields.Char("Texto", required=True)
    valor = fields.Char("Web o teléfono")

    def _para_el_portal(self):
        self.ensure_one()
        boton = {"tipo": TIPO_BOTON_META[self.tipo], "texto": self.texto}
        if self.tipo == "url":
            boton["url"] = self.valor
        elif self.tipo == "telefono":
            boton["telefono"] = self.valor
        return boton
