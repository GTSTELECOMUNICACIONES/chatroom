import { DiscussClientAction } from "@mail/core/public_web/discuss_client_action";
import { DiscussApp } from "@mail/core/public_web/discuss_app_model";
import { DiscussAppCategory } from "@mail/discuss/core/public_web/discuss_app_category_model";
import { fields } from "@mail/core/common/record";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

/**
 * LOS CHATS DENTRO DEL CHATROOM (David, 25-11-2026).
 *
 * 1. El apartado de la pasarela de la OCA se llama «ChatRoom» y no «Pasarela»:
 *    es por donde se escribe con el ChatRoom, y convive con el «WhatsApp» del
 *    módulo oficial, que sigue en su apartado.
 * 2. El menú «Chats» del ChatRoom abre la misma pantalla de conversaciones de
 *    Odoo (`mail.action_discuss`) con `gts_chatroom` en el contexto: dentro de
 *    la app, con sus menús, y solo con el apartado del ChatRoom. Desde el
 *    icono de conversaciones de siempre se sigue viendo todo.
 */

// La vista que se está pintando: la del ChatRoom o la de siempre. Variable
// del módulo y no un campo del store: lo fija la acción antes de pintar.
const vista = { soloChatroom: false };

patch(DiscussApp.prototype, {
    setup(env) {
        super.setup(env);
        // Mismo apartado que el de mail_gateway (id «gateway»), otro nombre.
        this.gateway = fields.One("DiscussAppCategory", {
            compute() {
                return {
                    extraClass: "o-mail-DiscussSidebarCategory-gateway",
                    id: "gateway",
                    name: _t("ChatRoom"),
                    icon: "fa fa-comments",
                    canView: false,
                    canAdd: true,
                    addTitle: _t("Buscar una conversación del ChatRoom"),
                    serverStateKey: "is_discuss_sidebar_category_gateway_open",
                };
            },
            eager: true,
        });
    },
});

patch(DiscussAppCategory.prototype, {
    get isVisible() {
        if (vista.soloChatroom && this.id !== "gateway") {
            return false;
        }
        return super.isVisible;
    },
});

patch(DiscussClientAction.prototype, {
    setup() {
        super.setup(...arguments);
        // Antes de pintar: el lateral se dibuja ya filtrado.
        vista.soloChatroom = Boolean(this.props.action?.context?.gts_chatroom);
    },
});
