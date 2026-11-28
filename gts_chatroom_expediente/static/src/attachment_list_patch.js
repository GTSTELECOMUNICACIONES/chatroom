import { AttachmentList } from "@mail/core/common/attachment_list";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

/**
 * «GUARDAR EN EL EXPEDIENTE» JUNTO A «DESCARGAR» (David, 28-11-2026), en los
 * adjuntos de las conversaciones del ChatRoom (canales `gateway`).
 *
 * Comprobado contra ~/ref/odoo-18.0 (mail/static/src/core/common/
 * attachment_list.js y .xml):
 * - las imágenes sacan sus botones de `getActions(attachment)`: se añade uno;
 * - los documentos (PDF, Word…) pintan «Descargar» en la tarjeta: al lado va
 *   un botón propio (attachment_list_patch.xml).
 * La conversación sale de `env.message` (lo pone el componente Message).
 * Quién puede guardar lo decide el servidor (el asistente es del grupo de
 * la pasarela y crear el adjunto exige poder escribir en el expediente).
 */
patch(AttachmentList.prototype, {
    gtsPuedeGuardar(attachment) {
        const thread = this.env.message?.thread;
        return Boolean(
            !this.env.inComposer &&
                !attachment.uploading &&
                attachment.type !== "url" &&
                typeof attachment.id === "number" &&
                thread?.model === "discuss.channel" &&
                thread.channel_type === "gateway"
        );
    },
    gtsGuardarEnExpediente(attachment) {
        this.env.services.action.doAction(
            "gts_chatroom_expediente.action_gts_chatroom_expediente_guardar",
            {
                additionalContext: {
                    default_adjunto_id: attachment.id,
                    default_canal_id: this.env.message.thread.id,
                },
            }
        );
    },
    getActions(attachment) {
        const acciones = super.getActions(attachment);
        if (this.gtsPuedeGuardar(attachment)) {
            acciones.push({
                label: _t("Guardar en el expediente"),
                icon: "fa fa-folder-open",
                onSelect: () => this.gtsGuardarEnExpediente(attachment),
            });
        }
        return acciones;
    },
});
