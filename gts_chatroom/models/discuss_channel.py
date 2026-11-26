import re
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.mail.tools.discuss import Store

RELEVO = [
    ("ia", "La IA"),
    ("humano", "Una persona"),
    ("resuelta", "Resuelta"),
]

# LA COLA SE DERIVA AQUI, de las piezas que manda el portal. Si el portal
# mandara la etiqueta ya calculada, se perderia lo que el no previo. Y se
# perdia: «humano, sin asignar, ya contestada» -el dueno contesta desde su
# movil antes de que nadie la coja- no cabia en cuatro valores y tuvo el
# buzon mudo un dia entero (Carlos, 19-11-2026). Las cuatro ultimas
# conversaciones archivadas en GTS Racing acabaron asi.
#
# EL ORDEN DE ESTA LISTA ES EL DE LA PANTALLA: con `group_expand` Odoo pinta
# los grupos en el orden en que se definen (`fields_selection.py`,
# `_default_group_expand`), asi que «Esperando» sale siempre arriba, tambien
# cuando esta vacio. Cambiar el orden no toca los datos guardados.
COLA = [
    ("esperando", "Esperando a una persona"),
    ("contestada", "Contestada, sin asignar"),
    ("asignada", "Le atiende un operador"),
    ("ia", "Con la IA"),
    ("resuelta", "Resuelta"),
]

# Minutos en «esperando» antes de crear la actividad, si no hay ajuste.
MINUTOS_AVISO = 5
PARAM_MINUTOS = "gts_chatroom.minutos_aviso"
PARAM_RESPONSABLE = "gts_chatroom.responsable_id"


def _escapar_like(texto):
    """`=ilike` trata `_` y `%` como comodines; un correo puede llevar `_`."""
    return re.sub(r"([\\%_])", r"\\\1", texto or "")


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    alma_es = fields.Boolean(
        string="Del portal de Alma",
        compute="_compute_alma_es",
        store=True,
    )
    alma_conversacion_id = fields.Char(
        string="Conversacion en el portal", index=True, readonly=True
    )
    # EL RELEVO MANDA EL PORTAL; ESTO ES UNA COPIA PARA PINTAR. Se cura sola:
    # cada mensaje entrante trae el estado dentro, asi que perder un aviso no
    # la deja mintiendo para siempre. Hueco aceptado: una conversacion sin
    # trafico no se cura, pero es justo la que nadie mira.
    alma_relevo = fields.Selection(RELEVO, string="Relevo", readonly=True)
    alma_asignado_a = fields.Char(string="La atiende", readonly=True)
    alma_espera = fields.Boolean(string="Espera respuesta", readonly=True)
    # Para el aviso de «Resuelta»: no se guarda, se mira al pintar.
    alma_ultimo_cliente = fields.Boolean(compute="_compute_alma_ultimo_cliente")
    alma_motivo_relevo = fields.Char(string="Motivo del relevo", readonly=True)

    @api.model
    def _gts_rotulos_de_la_cola(self):
        """Los rótulos de la cola en TODOS los idiomas activos. Al actualizar,
        Odoo solo reescribe el en_US y una instancia en español seguiría
        enseñando el viejo («La atiende una persona» → «Le atiende un
        operador», David, 25-11-2026). Lo llama data/rotulos_data.xml en cada
        actualización."""
        opciones = (
            self.env["ir.model.fields.selection"]
            .sudo()
            .search(
                [
                    ("field_id.model", "=", "discuss.channel"),
                    ("field_id.name", "=", "alma_cola"),
                ]
            )
        )
        rotulos = dict(COLA)
        idiomas = ["en_US", *self.env["res.lang"].search([]).mapped("code")]
        for opcion in opciones.filtered(lambda o: o.value in rotulos):
            for idioma in set(idiomas):
                opcion.with_context(lang=idioma).name = rotulos[opcion.value]
        return True

    alma_cola = fields.Selection(
        COLA,
        string="Cola",
        compute="_compute_alma_cola",
        store=True,
        index=True,
        group_expand=True,
    )
    alma_estado_en = fields.Datetime(
        string="Estado a fecha de",
        readonly=True,
        help="Emision del evento que trajo el estado. Uno mas viejo no lo pisa: "
        "los eventos pueden llegar desordenados.",
    )
    alma_esperando_desde = fields.Datetime(string="Esperando desde", readonly=True)
    alma_ultimo_mensaje = fields.Datetime(string="Ultimo mensaje", readonly=True)
    alma_es_mia = fields.Boolean(
        string="La atiendo yo",
        compute="_compute_alma_es_mia",
        search="_search_alma_es_mia",
    )
    # EL AVISO ES UN REGISTRO, NO UNA NOTIFICACION: una notificacion que suena
    # una vez se la pierde quien no estaba delante, que es justo a quien hay
    # que avisar. Una por cada vez que entra en espera; se cierra sola al
    # salir de «esperando».
    alma_actividad_id = fields.Many2one(
        "mail.activity",
        string="Aviso de espera",
        readonly=True,
        copy=False,
        ondelete="set null",
    )

    @api.depends("gateway_id.gateway_type")
    def _compute_alma_es(self):
        for channel in self:
            channel.alma_es = channel.gateway_id.gateway_type == "alma"

    @api.depends("alma_relevo", "alma_asignado_a", "alma_espera")
    def _compute_alma_cola(self):
        for channel in self:
            if channel.alma_relevo != "humano":
                channel.alma_cola = channel.alma_relevo or False
            elif channel.alma_asignado_a:
                channel.alma_cola = "asignada"
            elif channel.alma_espera:
                channel.alma_cola = "esperando"
            else:
                channel.alma_cola = "contestada"

    @api.depends("alma_asignado_a")
    @api.depends_context("uid")
    def _compute_alma_es_mia(self):
        correo = (self._alma_correo(self.env.user) or "").lower()
        for channel in self:
            channel.alma_es_mia = bool(
                correo and (channel.alma_asignado_a or "").lower() == correo
            )

    def _search_alma_es_mia(self, operator, value):
        if operator not in ("in", "not in"):
            return NotImplemented
        correo = _escapar_like(self._alma_correo(self.env.user))
        if (True in value) == (operator == "in"):
            return [("alma_asignado_a", "=ilike", correo)]
        return [
            "|",
            ("alma_asignado_a", "=", False),
            ("alma_asignado_a", "not =ilike", correo),
        ]

    @api.model
    def _alma_correo(self, usuario):
        """Como se identifica a una persona ante el portal: su correo, o el
        usuario de entrada si no tiene. Es lo que viaja en `asignado_a`."""
        return usuario.email or usuario.login

    # ------------------------------------------------------------------
    # La copia del estado
    # ------------------------------------------------------------------

    def _alma_aplicar_estado(self, estado, emitido_en, confirmado=False):
        """Copia las piezas del relevo que manda el portal, si no son mas
        viejas que las que ya hay. Se compara por `emitido_en`, el reloj del
        portal al emitir, nunca por la fecha del mensaje.

        `confirmado`: el cambio lo acaba de aceptar el portal en una llamada
        nuestra. Es lo mas nuevo que se sabe aunque los relojes no casen, asi
        que no se descarta por viejo."""
        self.ensure_one()
        relevo = estado.get("relevo")
        espera = bool(estado.get("espera"))
        if relevo == "esperando":
            # La forma de la version 0.1 del contrato, por si llega alguna.
            relevo, espera = "humano", True
        if relevo not in dict(RELEVO):
            return
        if self.alma_estado_en and emitido_en < self.alma_estado_en:
            if not confirmado:
                return
            emitido_en = self.alma_estado_en
        antes = self.alma_cola
        self.sudo().write(
            {
                "alma_relevo": relevo,
                "alma_asignado_a": estado.get("asignado_a") or False,
                "alma_espera": espera,
                "alma_motivo_relevo": estado.get("motivo_relevo") or False,
                "alma_estado_en": emitido_en,
            }
        )
        self._gts_avisar_cola()
        # «Esperando desde» se fija al ENTRAR en espera y no se mueve con cada
        # mensaje: la cola se ordena por cuanto lleva esperando.
        if self.alma_cola == "esperando" and antes != "esperando":
            self.sudo().alma_esperando_desde = emitido_en
            self._alma_programar_aviso()
            self._alma_avisar_al_entrar_en_espera()
        elif self.alma_cola != "esperando":
            self.sudo().alma_esperando_desde = False
            self._alma_cerrar_aviso()
        if self.alma_cola == "resuelta" and antes != "resuelta":
            self._gts_cerrar_para_todos()

    # ------------------------------------------------------------------
    # La cola en la pantalla de Chats (David, 26-11-2026)
    # ------------------------------------------------------------------

    def _gts_datos_cola(self):
        """Lo que necesita el lateral de Chats para poner la conversación en
        su apartado. «Para mí» lo decide el navegador comparando
        `gts_asignado_a` con el correo del usuario (`ir.http`): este mismo
        dato viaja por el bus a todos los miembros y no puede llevar «mía»."""
        self.ensure_one()
        return {
            "id": self.id,
            "gts_cola": self.alma_cola or False,
            "gts_asignado_a": (self.alma_asignado_a or "").lower() or False,
            "gts_canal_id": self.gateway_id.id or False,
        }

    @api.model
    def gts_buzones(self):
        """Los buzones (canales del portal) para la cabecera de Chats: su
        nombre, el número por el que se atiende y cuántas esperan. Con sudo:
        la pasarela solo la lee un administrador. Si el usuario es miembro de
        alguno, solo esos."""
        if not self.env.user.has_group("mail_gateway.gateway_user"):
            return []
        canales = (
            self.env["mail.gateway"]
            .sudo()
            .search(
                [
                    ("gateway_type", "=", "alma"),
                    ("integrated_webhook_state", "=", "integrated"),
                ],
                order="id",
            )
        )
        mios = canales.filtered(lambda g: self.env.user in g.member_ids)
        esperando = self.env["mail.gateway"]._gts_esperando_por_canal()
        return [
            {
                "id": canal.id,
                "nombre": canal.name or canal.canal_etiqueta,
                "canal": canal.canal_etiqueta,
                "numero": canal.canal_numero or "",
                "esperando": esperando.get(canal.id, 0),
            }
            for canal in (mios or canales)
        ]

    def action_gts_descartar(self):
        """DESCARTAR UNA CONVERSACIÓN ABIERTA SIN ESCRIBIR (David,
        26-11-2026): «Abrir la conversación» la crea, y si al final no se le
        manda nada se queda en el lateral. Se borra SOLO si está vacía y el
        portal no la conoce todavía; con mensajes, se cierra con «Resuelta»."""
        self.ensure_one()
        if not self.alma_es:
            raise UserError(self.env._("Esta conversación no es del ChatRoom."))
        con_mensajes = (
            self.env["mail.message"]
            .sudo()
            .search_count(
                [
                    ("model", "=", "discuss.channel"),
                    ("res_id", "=", self.id),
                    ("message_type", "=", "comment"),
                ],
                limit=1,
            )
        )
        if con_mensajes or self.alma_conversacion_id:
            raise UserError(
                self.env._(
                    "Esta conversación ya tiene mensajes: no se borra. Ciérrala "
                    "con «Dar por resuelta»."
                )
            )
        self.sudo().unlink()
        return True

    def action_gts_pasar(self):
        """Abre «Pasar a otra persona». Se llama con `orm.call` desde Chats,
        no como botón: ese camino no pasa por `clean_action`, que es quien
        rellena `views` a partir de `view_mode`, y sin `views` el cliente
        revienta («reading 'map'»)."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Pasar a otra persona"),
            "res_model": "gts.chatroom.pasar",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_canal_id": self.id},
        }

    def _to_store(self, store: Store, fields):
        res = super()._to_store(store, fields)
        for canal in self.filtered("alma_es"):
            store.add_model_values(self._name, canal._gts_datos_cola())
        return res

    def _gts_cerrar_para_todos(self):
        """RESUELTA = CERRADA (David, 26-11-2026): se quita del lateral de
        todos los operadores, como cerrar un chat. No se borra nada: si el
        cliente vuelve a escribir, el mensaje nuevo la vuelve a fijar
        (`is_pinned` compara con `last_interest_dt` del canal) y entra otra
        vez por su cola."""
        ahora = fields.Datetime.now()
        for canal in self.sudo():
            canal.channel_member_ids.filtered("partner_id").write({"unpin_dt": ahora})

    def _gts_avisar_cola(self):
        """La conversación cambia de apartado en el lateral sin recargar."""
        for canal in self.filtered("alma_es"):
            Store(bus_channel=canal).add_model_values(
                canal._name, canal._gts_datos_cola()
            ).bus_send()

    def _get_message_create_valid_field_names(self):
        # Una plantilla se publica como un mensaje más de la conversación
        # (gts.chatroom.nueva): `_send` la manda como plantilla.
        return super()._get_message_create_valid_field_names() | {
            "alma_plantilla_id",
            "alma_parametros",
        }

    # ------------------------------------------------------------------
    # Los botones de la cola
    # ------------------------------------------------------------------

    def action_alma_atender(self):
        """Me la quedo: relevo a una persona, asignada a mi."""
        return self._alma_cambiar_relevo("humano")

    def action_alma_devolver_ia(self):
        return self._alma_cambiar_relevo("ia")

    def action_alma_resolver(self):
        return self._alma_cambiar_relevo("resuelta")

    def _alma_cambiar_relevo(self, relevo, para=None):
        """LA COPIA DE ODOO NUNCA SE ADELANTA AL PORTAL. Primero se le pide el
        cambio al portal; solo si lo acepta se toca la conversacion aqui. Si
        falla, el error sube como UserError, la transaccion se deshace y la
        conversacion se queda como estaba.

        `para`: pasarla a otra persona (David, 26-11-2026). Es el mismo
        relevo «humano» con el correo de esa persona en `asignado_a`; si no es
        miembro de la cuenta en el portal, vuelve «operador_no_es_miembro»."""
        self.ensure_one()
        if not self.alma_es:
            raise UserError(self.env._("Esta conversación no es del portal de Alma."))
        if not self.env.user.has_group("mail_gateway.gateway_user"):
            raise UserError(
                self.env._("No tiene permiso para atender conversaciones de WhatsApp.")
            )
        self.check_access("read")
        # «Resuelta» con el ultimo mensaje del cliente SE AVISA, no se impide
        # (acordado con Carlos, 21-11-2026): puede haberse resuelto por
        # telefono o en persona, y quien pulsa lo sabe. La regla de «nunca se
        # archiva» es para el barrido automatico, donde nadie mira. El aviso
        # es el `confirm` del boton, con `alma_ultimo_cliente`.
        if not self.alma_conversacion_id:
            raise UserError(
                self.env._(
                    "Esta conversación todavía no tiene su identificador del "
                    "portal, así que no se le puede avisar. Espere al siguiente "
                    "mensaje del cliente o pulse «Recuperar del portal» en la "
                    "pasarela."
                )
            )
        usuario = para or self.env.user
        quien = self.env.user
        # La hora de ANTES de llamar: cualquier evento que el portal emita
        # despues de aplicar el cambio sera mas nuevo y pisara a este.
        antes_de_llamar = fields.Datetime.now()
        Alma = self.env["mail.gateway.alma"]
        datos, error = Alma._alma_relevo(self.sudo().gateway_id, self, relevo, usuario)
        estado = datos.get("estado") if isinstance(datos.get("estado"), dict) else {}
        if not estado:
            # El portal no devolvio el estado: se pone el que se le pidio.
            estado = {
                "relevo": relevo,
                "asignado_a": (
                    self._alma_correo(usuario) if relevo == "humano" else None
                ),
                "espera": self.alma_espera if relevo == "humano" else False,
                "motivo_relevo": (
                    self.alma_motivo_relevo if relevo == "humano" else None
                ),
            }
        emitido_en = antes_de_llamar
        if datos.get("emitido_en"):
            try:
                emitido_en = Alma._alma_fecha(datos["emitido_en"])
            except ValueError:
                emitido_en = antes_de_llamar
        self._alma_aplicar_estado(estado, emitido_en, confirmado=True)
        if error:
            # El portal no hizo lo pedido pero dijo como queda: se copia eso y
            # se ensena el motivo, sin deshacer la copia.
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "danger",
                    "sticky": True,
                    "message": error,
                    "next": {"type": "ir.actions.client", "tag": "soft_reload"},
                },
            }
        if relevo == "humano" and usuario != quien:
            texto = self.env._(
                "%(quien)s pasa la conversación a %(a)s.",
                quien=quien.name,
                a=usuario.name,
            )
        elif relevo == "humano":
            texto = self.env._("%s atiende la conversación.", usuario.name)
        elif relevo == "ia":
            texto = self.env._("%s devuelve la conversación a la IA.", usuario.name)
        else:
            texto = self.env._("%s da la conversación por resuelta.", usuario.name)
        # Una linea en la propia conversacion, para que se vea quien la cogio.
        # Tipo «notification»: la pasarela de la OCA no manda esas al cliente.
        self.sudo().with_context(no_gateway_notification=True).message_post(
            body=Markup("<p>%s</p>") % texto,
            author_id=quien.partner_id.id,
            message_type="notification",
            subtype_xmlid="mail.mt_note",
        )
        if relevo == "humano":
            # Coger la conversación es unirse a ella: aparece en «Para mí»
            # aunque no fuera miembro.
            self.sudo()._add_members(users=usuario, post_joined_message=False)
            if self.env.context.get("gts_desde_chats"):
                return False
            return self.action_alma_abrir()
        if self.env.context.get("gts_desde_chats"):
            return False
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def _compute_alma_ultimo_cliente(self):
        for canal in self:
            canal.alma_ultimo_cliente = bool(
                canal.id and canal.alma_es and canal._alma_ultimo_es_del_cliente()
            )

    def _alma_ultimo_es_del_cliente(self):
        self.ensure_one()
        ultimo = (
            self.env["mail.message"]
            .sudo()
            .search(
                [
                    ("model", "=", "discuss.channel"),
                    ("res_id", "=", self.id),
                    ("message_type", "=", "comment"),
                ],
                order="date desc, id desc",
                limit=1,
            )
        )
        return ultimo.alma_origen == "contacto"

    # ------------------------------------------------------------------
    # El aviso cuando nadie la coge
    # ------------------------------------------------------------------

    @api.model
    def _alma_minutos_aviso(self):
        valor = (
            self.env["ir.config_parameter"].sudo().get_param(PARAM_MINUTOS) or ""
        ).strip()
        try:
            return max(1, int(valor))
        except ValueError:
            return MINUTOS_AVISO

    def _alma_programar_aviso(self):
        """Despierta el cron justo cuando toque, en vez de esperar a su vuelta
        de cada cuarto de hora."""
        cron = self.env.ref(
            "gts_chatroom.ir_cron_alma_avisar_esperas", raise_if_not_found=False
        )
        if not cron:
            return
        minutos = timedelta(minutes=self._alma_minutos_aviso())
        ahora = fields.Datetime.now()
        momentos = [
            max(ahora, (channel.alma_esperando_desde or ahora) + minutos)
            for channel in self
        ]
        cron.sudo()._trigger(momentos)

    def _alma_avisar_al_entrar_en_espera(self):
        """Un aviso emergente a quien atiende, en el momento en que la
        conversacion ENTRA en espera (no en cada evento: eso lo decide quien
        llama). Peticion de David, 21-11-2026: tomo el control y no tuvo
        ninguna senal de que alguien esperaba.

        SIN JAVASCRIPT NUESTRO: `simple_notification` es del bus de Odoo
        (`bus/static/src/simple_notification_service.js`), que lo pinta con
        el servicio de notificaciones. Un fallo en un componente propio de la
        barra superior ya dejo el backend en blanco una vez. El bus envia al
        hacer commit, asi que un reintento por concurrencia no avisa dos
        veces. Sin sonido: Odoo no lo trae para esto sin JS.

        Lo que ya habia se queda: la actividad a los N minutos y el contador
        del menu."""
        self.ensure_one()
        usuarios = self.sudo().gateway_id.member_ids.filtered("active")
        if not usuarios:
            usuarios = self._alma_responsable()
        if not usuarios:
            return
        motivo = self.alma_motivo_relevo
        mensaje = (
            self.env._(
                "%(nombre)s espera a una persona: %(motivo)s",
                nombre=self.name,
                motivo=motivo,
            )
            if motivo
            else self.env._("%(nombre)s espera a una persona.", nombre=self.name)
        )
        for usuario in usuarios:
            usuario._bus_send(
                "simple_notification",
                {
                    "type": "warning",
                    "title": self.env._("WhatsApp: alguien espera"),
                    "message": mensaje,
                    "sticky": True,
                },
            )

    def _alma_cerrar_aviso(self):
        for channel in self.filtered("alma_actividad_id"):
            actividad = channel.alma_actividad_id.sudo()
            channel.sudo().alma_actividad_id = False
            if actividad.exists() and actividad.active:
                actividad.action_feedback(
                    feedback=self.env._(
                        "Ya no espera: %s.",
                        dict(
                            self._fields["alma_cola"]._description_selection(self.env)
                        ).get(channel.alma_cola, ""),
                    )
                )

    @api.model
    def _alma_avisar_esperas(self):
        """Cron: una actividad al responsable por cada conversacion que lleva
        mas de N minutos esperando sin que nadie la coja."""
        limite = fields.Datetime.now() - timedelta(minutes=self._alma_minutos_aviso())
        canales = self.sudo().search(
            [
                ("alma_es", "=", True),
                ("alma_cola", "=", "esperando"),
                ("alma_actividad_id", "=", False),
                ("alma_esperando_desde", "<=", limite),
            ]
        )
        if not canales:
            return
        responsable = self._alma_responsable()
        if not responsable:
            return
        tipo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        modelo_contacto = self.env["ir.model"]._get_id("res.partner")
        for canal in canales:
            contacto = canal._get_gateway_partner()
            # La actividad va en el CONTACTO y no en el canal: crearla en un
            # canal intenta suscribir al responsable como seguidor, y
            # discuss.channel lo prohibe con un UserError
            # (`discuss_channel.py`, `_message_subscribe`).
            vals = {
                "user_id": responsable.id,
                "activity_type_id": tipo.id if tipo else False,
                "summary": self.env._("WhatsApp: %s espera a una persona", canal.name),
                "note": Markup(
                    '<p>%s</p><p><a href="/odoo/action-mail.action_discuss'
                    '?active_id=%s">%s</a></p>'
                )
                % (
                    self.env._(
                        "Lleva esperando desde %s y nadie la ha cogido todavía.",
                        fields.Datetime.context_timestamp(
                            self.with_context(tz=responsable.tz or "UTC"),
                            canal.alma_esperando_desde,
                        ).strftime("%d/%m/%Y %H:%M"),
                    ),
                    canal.id,
                    self.env._("Abrir la conversación"),
                ),
                "date_deadline": fields.Date.context_today(self),
            }
            if contacto:
                vals.update({"res_model_id": modelo_contacto, "res_id": contacto.id})
            canal.alma_actividad_id = self.env["mail.activity"].sudo().create(vals)

    @api.model
    def _alma_responsable(self):
        valor = self.env["ir.config_parameter"].sudo().get_param(PARAM_RESPONSABLE)
        responsable = self.env["res.users"]
        if valor:
            try:
                responsable = responsable.sudo().browse(int(valor)).exists()
            except ValueError:
                responsable = self.env["res.users"]
        if not responsable or not responsable.active:
            # Sin responsable puesto, el administrador: un aviso sin nadie a
            # quien le duela no es un aviso.
            responsable = self.env.ref("base.user_admin", raise_if_not_found=False)
        return responsable if responsable and responsable.active else False

    def action_alma_abrir(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            # En Chats del ChatRoom, no en las conversaciones de Odoo.
            "url": (
                f"/odoo/action-gts_chatroom.action_chatroom_chats?active_id={self.id}"
            ),
            "target": "self",
        }
