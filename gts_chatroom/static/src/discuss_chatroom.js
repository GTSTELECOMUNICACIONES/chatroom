import { Component } from "@odoo/owl";
import { threadActionsRegistry } from "@mail/core/common/thread_actions";
import { Thread } from "@mail/core/common/thread_model";
import { fields } from "@mail/core/common/record";
import { DiscussClientAction } from "@mail/core/public_web/discuss_client_action";
import { DiscussApp } from "@mail/core/public_web/discuss_app_model";
import {
    DiscussSidebar,
    discussSidebarItemsRegistry,
} from "@mail/core/public_web/discuss_sidebar";
import { DiscussAppCategory } from "@mail/discuss/core/public_web/discuss_app_category_model";
import { compareDatetime } from "@mail/utils/common/misc";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";

/**
 * LOS CHATS DENTRO DEL CHATROOM (David, 25 y 26-11-2026).
 *
 * 1. El menú «Chats» del ChatRoom abre la pantalla de conversaciones de Odoo
 *    (`mail.action_discuss`) con `gts_chatroom` en el contexto: dentro de la
 *    app y SOLO con lo del ChatRoom. Sin la «Bandeja de entrada», los
 *    destacados ni el historial de Odoo, que traen todo lo demás. Desde el
 *    icono de conversaciones de siempre se sigue viendo todo.
 * 2. El lateral se reparte POR COLA, como las bandejas: Sin atender, Para
 *    mí, Contestadas sin asignar, Con otros operadores, Atendidos por la IA
 *    y Resueltas. Cada apartado se despliega con sus chats. La cola la manda
 *    el servidor (`_gts_datos_cola`) al cargar y por el bus al cambiar.
 * 3. En la cabecera de cada chat: Atender (cogerla y unirse), Devolver a la
 *    IA y Resuelta. Los mismos botones que las tarjetas.
 * 4. «Nueva conversación»: contacto, canal y plantilla, sin salir de aquí.
 *
 * Lo que no es del portal (otras pasarelas) sigue en el apartado «ChatRoom».
 */

// La vista que se está pintando: la del ChatRoom o la de siempre. Variable
// del módulo y no un campo del store: lo fija la acción antes de pintar.
const vista = { soloChatroom: false };

// (id del apartado, nombre, icono). El orden es el de la pantalla.
const APARTADOS = [
    ["gts_esperando", _t("Sin atender"), "fa fa-hourglass-half"],
    ["gts_mias", _t("Para mí"), "fa fa-user"],
    ["gts_contestada", _t("Contestadas sin asignar"), "fa fa-reply"],
    ["gts_otros", _t("Con otros operadores"), "fa fa-users"],
    ["gts_ia", _t("Atendidos por la IA"), "fa fa-magic"],
    ["gts_resuelta", _t("Resueltas"), "fa fa-check"],
];

function esNuestro(id) {
    return id === "gateway" || String(id).startsWith("gts_");
}

function esMia(thread) {
    const correo = session.gts_chatroom_correo;
    return Boolean(correo && thread.gts_asignado_a === correo);
}

function apartadoDe(thread) {
    switch (thread.gts_cola) {
        case "esperando":
            return "gts_esperando";
        case "contestada":
            return "gts_contestada";
        case "asignada":
            return esMia(thread) ? "gts_mias" : "gts_otros";
        case "ia":
            return "gts_ia";
        case "resuelta":
            return "gts_resuelta";
    }
    return false;
}

patch(DiscussApp.prototype, {
    setup(env) {
        super.setup(env);
        // Mismo apartado que el de mail_gateway (id «gateway»), otro nombre.
        // Queda para lo que no es del portal y se esconde si está vacío.
        this.gateway = fields.One("DiscussAppCategory", {
            compute() {
                return {
                    extraClass: "o-mail-DiscussSidebarCategory-gateway",
                    id: "gateway",
                    name: _t("ChatRoom"),
                    icon: "fa fa-comments",
                    canView: false,
                    hideWhenEmpty: true,
                    sequence: 9,
                    serverStateKey: "is_discuss_sidebar_category_gateway_open",
                };
            },
            eager: true,
        });
        APARTADOS.forEach(([id, name, icon], n) => {
            this[id] = fields.One("DiscussAppCategory", {
                compute() {
                    return {
                        extraClass: `o-mail-DiscussSidebarCategory-${id}`,
                        id,
                        name,
                        icon,
                        canView: false,
                        sequence: 1 + n,
                    };
                },
                eager: true,
            });
        });
    },
});

patch(DiscussAppCategory.prototype, {
    get isVisible() {
        if (vista.soloChatroom && !esNuestro(this.id)) {
            return false;
        }
        // En las conversaciones de siempre, solo los apartados con algo: a
        // quien no atiende WhatsApp no se le llena el lateral de vacíos.
        if (!vista.soloChatroom && String(this.id).startsWith("gts_") && !this.threads.length) {
            return false;
        }
        return super.isVisible;
    },
    sortThreads(t1, t2) {
        if (String(this.id).startsWith("gts_")) {
            return compareDatetime(t2.lastInterestDt, t1.lastInterestDt) || t2.id - t1.id;
        }
        return super.sortThreads(t1, t2);
    },
});

patch(Thread.prototype, {
    setup() {
        super.setup();
        this.gts_cola = fields.Attr(false);
        this.gts_asignado_a = fields.Attr(false);
    },
    _computeDiscussAppCategory() {
        if (this.channel_type === "gateway" && this.gts_cola) {
            const apartado = apartadoDe(this);
            if (apartado) {
                return this.store.discuss[apartado];
            }
        }
        return super._computeDiscussAppCategory(...arguments);
    },
});

patch(DiscussSidebar.prototype, {
    get discussSidebarItems() {
        if (!vista.soloChatroom) {
            return super.discussSidebarItems;
        }
        // Sin la bandeja de entrada, los destacados ni el historial.
        return discussSidebarItemsRegistry
            .getEntries()
            .filter(([clave]) => clave !== "mailbox")
            .map(([, item]) => item);
    },
});

patch(DiscussClientAction.prototype, {
    setup() {
        super.setup(...arguments);
        // Antes de pintar: el lateral se dibuja ya filtrado.
        vista.soloChatroom = Boolean(this.props.action?.context?.gts_chatroom);
    },
    getActiveId(props) {
        const activeId = super.getActiveId(props);
        // La última vez se miró la bandeja de entrada: aquí no se abre.
        if (vista.soloChatroom && String(activeId || "").startsWith("mail.box")) {
            return undefined;
        }
        return activeId;
    },
});

// ---------------------------------------------------------------------
// Nueva conversación
// ---------------------------------------------------------------------

export class GtsNuevaConversacion extends Component {
    static template = "gts_chatroom.NuevaConversacion";
    static props = {};

    setup() {
        super.setup();
        this.store = useService("mail.store");
        this.action = useService("action");
    }

    get visible() {
        return vista.soloChatroom;
    }

    abrir() {
        this.action.doAction("gts_chatroom.action_chatroom_nueva", {
            onClose: async (info) => {
                if (!info?.gts_canal_id) {
                    return;
                }
                const thread = await this.store.Thread.getOrFetch({
                    model: "discuss.channel",
                    id: info.gts_canal_id,
                });
                thread?.setAsDiscussThread();
            },
        });
    }
}

discussSidebarItemsRegistry.add("gts_nueva_conversacion", GtsNuevaConversacion, {
    sequence: 25,
});

// ---------------------------------------------------------------------
// Atender, devolver a la IA y resolver, desde la cabecera del chat
// ---------------------------------------------------------------------

function esDelPortal(owner, thread) {
    return (
        thread?.model === "discuss.channel" &&
        thread.channel_type === "gateway" &&
        Boolean(thread.gts_cola) &&
        (!owner.props.chatWindow || owner.props.chatWindow.isOpen)
    );
}

async function cambiarRelevo(owner, thread, metodo) {
    const resultado = await owner.env.services.orm.call(
        "discuss.channel",
        metodo,
        [[thread.id]],
        { context: { gts_desde_chats: true } }
    );
    // Si el portal no lo aceptó, vuelve el aviso con el motivo.
    if (resultado) {
        await owner.env.services.action.doAction(resultado);
    }
}

threadActionsRegistry
    .add("gts-atender", {
        condition: ({ owner, thread }) =>
            esDelPortal(owner, thread) && !(thread.gts_cola === "asignada" && esMia(thread)),
        icon: "fa fa-fw fa-hand-paper-o",
        iconLarge: "fa fa-fw fa-lg fa-hand-paper-o",
        name: _t("Atender: me la quedo"),
        open: ({ owner, thread }) => cambiarRelevo(owner, thread, "action_alma_atender"),
        sequence: 5,
    })
    .add("gts-devolver-ia", {
        condition: ({ owner, thread }) => esDelPortal(owner, thread) && thread.gts_cola !== "ia",
        icon: "fa fa-fw fa-magic",
        iconLarge: "fa fa-fw fa-lg fa-magic",
        name: _t("Devolver a la IA"),
        open: ({ owner, thread }) => cambiarRelevo(owner, thread, "action_alma_devolver_ia"),
        sequence: 6,
    })
    .add("gts-resolver", {
        condition: ({ owner, thread }) =>
            esDelPortal(owner, thread) && thread.gts_cola !== "resuelta",
        icon: "fa fa-fw fa-check",
        iconLarge: "fa fa-fw fa-lg fa-check",
        name: _t("Dar por resuelta"),
        open: ({ owner, thread }) => cambiarRelevo(owner, thread, "action_alma_resolver"),
        sequence: 7,
    });
