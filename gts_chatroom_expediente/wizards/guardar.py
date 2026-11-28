"""«GUARDAR EN EL EXPEDIENTE» UN ADJUNTO DE UNA CONVERSACIÓN (David,
28-11-2026).

Junto a «Descargar», en cada adjunto que llega por una conversación del
ChatRoom: se elige el expediente y el fichero se COPIA allí sin pasar por el
disco de nadie. Copia y no enlace, por lo mismo que `gts_expediente_archivo`
(`ir_attachment.gts_archivar_en`): lo que se archiva en el expediente no
puede desaparecer porque alguien limpie la conversación, ni al revés.

- Siempre: el adjunto queda en el expediente (`ir.attachment` del
  `project.project`) con una nota en su historial que dice de dónde vino.
- Si está `gts_expediente_archivo` (el puente al DMS: `gts_archivar`), además
  se archiva en una carpeta del DMS del expediente: la que se escriba, o
  «04 Documentación del cliente» por defecto (su resolutor). Se comprueba
  con `hasattr`: este módulo NO depende del DMS.
"""

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError


class GtsChatroomExpedienteGuardar(models.TransientModel):
    _name = "gts.chatroom.expediente.guardar"
    _description = "Guardar en el expediente un adjunto del ChatRoom"

    adjunto_id = fields.Many2one(
        "ir.attachment", "Documento", required=True, readonly=True, ondelete="cascade"
    )
    canal_id = fields.Many2one(
        "discuss.channel", "Conversación", readonly=True, ondelete="cascade"
    )
    partner_id = fields.Many2one(
        "res.partner", "Contacto", compute="_compute_partner_id"
    )
    expediente_id = fields.Many2one(
        "project.project",
        "Expediente",
        required=True,
        domain="[('es_expediente', '=', True)]",
        help="Si el contacto de la conversación tiene un solo expediente, "
        "viene puesto.",
    )
    hay_carpetas = fields.Boolean(compute="_compute_carpetas")
    carpetas_texto = fields.Char("Carpetas del expediente", compute="_compute_carpetas")
    carpeta = fields.Char(
        "Carpeta",
        help="La carpeta de documentos del expediente donde se archiva. Vale "
        "con una parte del nombre («escritos», «04»). Vacía: «04 "
        "Documentación del cliente».",
    )
    aviso = fields.Text(compute="_compute_aviso")

    @api.depends("canal_id")
    def _compute_partner_id(self):
        for asistente in self:
            asistente.partner_id = (
                asistente.canal_id._gts_contacto_de_la_pasarela()
                if asistente.canal_id
                else False
            )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "expediente_id" in fields_list and not res.get("expediente_id"):
            canal = self.env["discuss.channel"].browse(res.get("canal_id") or [])
            contacto = canal.exists() and canal._gts_contacto_de_la_pasarela()
            if contacto:
                expedientes = self._gts_expedientes_de(contacto)
                if len(expedientes) == 1:
                    res["expediente_id"] = expedientes.id
        return res

    @api.model
    def _gts_expedientes_de(self, contacto):
        # La empresa y sus personas: el cliente del expediente puede ser la
        # sociedad y quien escribe por WhatsApp, su administrador.
        return self.env["project.project"].search(
            [
                ("es_expediente", "=", True),
                ("partner_id", "child_of", contacto.commercial_partner_id.id),
            ]
        )

    @api.depends("expediente_id")
    def _compute_carpetas(self):
        for asistente in self:
            carpetas = asistente._gts_carpetas_dms()
            asistente.hay_carpetas = bool(carpetas)
            asistente.carpetas_texto = (
                ", ".join(c["nombre"] for c in carpetas) if carpetas else False
            )

    def _gts_carpetas_dms(self):
        """Las carpetas del DMS del expediente, si está el puente que las
        sabe leer (`gts_expediente_archivo`); si no, ninguna."""
        self.ensure_one()
        expediente = self.expediente_id
        if not expediente or not hasattr(expediente, "gts_carpetas"):
            return []
        return expediente.gts_carpetas()

    @api.depends("expediente_id", "partner_id", "adjunto_id")
    def _compute_aviso(self):
        for asistente in self:
            avisos = []
            contacto = asistente.partner_id
            if contacto:
                cuantos = len(self._gts_expedientes_de(contacto))
                if cuantos > 1:
                    avisos.append(
                        self.env._(
                            "%(c)s tiene %(n)s expedientes. Comprueba que este "
                            "es el que toca.",
                            c=contacto.display_name,
                            n=cuantos,
                        )
                    )
                elif not cuantos:
                    avisos.append(
                        self.env._(
                            "%s no es cliente de ningún expediente: elige el "
                            "expediente a mano.",
                            contacto.display_name,
                        )
                    )
            elif asistente.canal_id:
                avisos.append(
                    self.env._(
                        "Quien escribe no está en Contactos todavía: elige el "
                        "expediente a mano."
                    )
                )
            if asistente.expediente_id and asistente._gts_ya_esta():
                avisos.append(
                    self.env._("Este documento ya está guardado en este expediente.")
                )
            asistente.aviso = "\n".join(avisos) or False

    def _gts_ya_esta(self):
        """La copia que ya haya en el expediente (mismo contenido)."""
        self.ensure_one()
        if not self.adjunto_id.checksum:
            return self.env["ir.attachment"]
        return self.env["ir.attachment"].search(
            [
                ("res_model", "=", "project.project"),
                ("res_id", "=", self.expediente_id.id),
                ("checksum", "=", self.adjunto_id.checksum),
            ],
            limit=1,
        )

    def _gts_origen(self):
        self.ensure_one()
        quien = self.partner_id.display_name or self.canal_id.display_name
        return self.env._(
            "Recibido por el ChatRoom de %(quien)s (conversación «%(conv)s»).",
            quien=quien or "?",
            conv=self.canal_id.display_name or "?",
        )

    def action_guardar(self):
        self.ensure_one()
        expediente = self.expediente_id
        if not expediente.es_expediente:
            raise UserError(self.env._("Ese proyecto no es un expediente."))
        # `bin_size=False` es imprescindible: con `bin_size` (lo pone el
        # cliente web) el contenido que se lee es el TAMAÑO en texto y se
        # copiaría un fichero de nueve bytes sin ningún error.
        adjunto = self.adjunto_id.with_context(bin_size=False)
        contenido = adjunto.raw
        if not contenido:
            raise UserError(self.env._("Ese documento no tiene contenido."))
        copia = self._gts_ya_esta()
        ya_estaba = bool(copia)
        if not copia:
            copia = self.env["ir.attachment"].create(
                {
                    "name": adjunto.name,
                    "raw": contenido,
                    "mimetype": adjunto.mimetype,
                    "res_model": "project.project",
                    "res_id": expediente.id,
                    "description": self._gts_origen(),
                }
            )
            expediente.message_post(
                body=Markup("<p>%s</p>") % self._gts_origen(),
                attachment_ids=copia.ids,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        partes = [
            (
                self.env._("Ya estaba en el expediente %s.", expediente.display_name)
                if ya_estaba
                else self.env._(
                    "Guardado en el expediente %s.", expediente.display_name
                )
            )
        ]
        tipo = "success"
        if self._gts_carpetas_dms():
            try:
                with self.env.cr.savepoint():
                    resultado = expediente.gts_archivar(
                        copia.ids, carpeta=(self.carpeta or "").strip() or None
                    )
                partes.append(resultado.get("resumen") or "")
                if resultado.get("fallidos"):
                    tipo = "warning"
            except UserError as error:
                # El adjunto ya está en el expediente; lo que falla es solo
                # la carpeta (no existe, o el nombre casa con varias).
                tipo = "warning"
                partes.append(
                    self.env._("No se ha archivado en una carpeta: %s", error)
                )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Expediente"),
                "message": " ".join(p for p in partes if p),
                "type": tipo,
                "sticky": tipo != "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
