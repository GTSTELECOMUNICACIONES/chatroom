import { Component, onMounted, onWillStart, onWillUnmount, reactive, useState } from "@odoo/owl";
import { threadActionsRegistry } from "@mail/core/common/thread_actions";
import { Thread } from "@mail/core/common/thread_model";
import { fields } from "@mail/core/common/record";
import { DiscussClientAction } from "@mail/core/public_web/discuss_client_action";
import { DiscussApp } from "@mail/core/public_web/discuss_app_model";
import {
    DiscussSidebar,
    discussSidebarItemsRegistry,
} from "@mail/core/public_web/discuss_sidebar";
import {
    DiscussSidebarCategories,
    DiscussSidebarCategory,
} from "@mail/discuss/core/public_web/discuss_sidebar_categories";
import { DiscussAppCategory } from "@mail/discuss/core/public_web/discuss_app_category_model";
import { compareDatetime } from "@mail/utils/common/misc";
import { browser } from "@web/core/browser/browser";
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
 * 5. Arriba, EL BUZÓN: el nombre del canal y el número por el que se
 *    atiende, para no contestar desde el que no es. Con varios, se elige.
 *    Mientras haya conversaciones sin atender, una bolita roja parpadea y
 *    pita cada pocos segundos (se puede silenciar).
 * 6. «Pasar a otra persona» en la cabecera del chat.
 *
 * Lo que no es del portal (otras pasarelas) sigue en el apartado «ChatRoom».
 */

// La vista que se está pintando: la del ChatRoom o la de siempre. Variable
// del módulo y no un campo del store: lo fija la acción antes de pintar.
const vista = { soloChatroom: false };

// El buzón elegido en el lateral (false = todos los míos). Reactivo: al
// cambiarlo se repinta. Clave nueva: la vieja guardaba un canal, no un buzón.
const CLAVE_BUZON = "gts_chatroom.buzon_id";
const CLAVE_SILENCIO = "gts_chatroom.silencio";
const filtro = reactive({ buzon: leerNumero(CLAVE_BUZON) });

function leerNumero(clave) {
    try {
        return parseInt(browser.localStorage.getItem(clave)) || false;
    } catch {
        return false;
    }
}

function delBuzon(thread) {
    return !filtro.buzon || thread.gts_buzon_id === filtro.buzon;
}

// (id del apartado, nombre, icono). El orden es el de la pantalla.
const APARTADOS = [
    // La BANDEJA DE ENTRADA: donde entra todo lo que nadie ha cogido
    // (David, 26-11-2026). Siempre abierta, con contador y bolita.
    ["gts_esperando", _t("Bandeja de entrada (sin atender)"), "fa fa-inbox"],
    ["gts_mias", _t("Para mí"), "fa fa-user-circle-o"],
    ["gts_contestada", _t("Contestadas sin asignar"), "fa fa-reply"],
    ["gts_otros", _t("Atendidas por compañeros"), "fa fa-users"],
    ["gts_ia", _t("Atendidos por la IA"), "fa fa-magic"],
    ["gts_resuelta", _t("Resueltas"), "fa fa-check-circle-o"],
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
    get open() {
        // La bandeja de entrada no se puede plegar: lo que espera, se ve.
        return this.id === "gts_esperando" ? true : super.open;
    },
    set open(value) {
        if (this.id !== "gts_esperando") {
            super.open = value;
        }
    },
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
        this.gts_canal_id = fields.Attr(false);
        this.gts_buzon_id = fields.Attr(false);
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

patch(DiscussSidebarCategory.prototype, {
    /** El icono de cada apartado del ChatRoom, delante del nombre. */
    get gtsIcono() {
        return String(this.category.id).startsWith("gts_") ? this.category.icon : false;
    },
    /** Las que esperan en la bandeja de entrada (del buzón elegido). */
    get gtsPendientes() {
        if (this.category.id !== "gts_esperando") {
            return 0;
        }
        return this.category.threads.filter((t) => t.displayInSidebar && delBuzon(t))
            .length;
    },
    get actions() {
        const actions = super.actions;
        if (this.category.id === "gts_resuelta") {
            // El histórico entero, con buscador: al resolverse salen del
            // lateral, pero hay que poder encontrarlas (calidad).
            actions.push({
                onSelect: () =>
                    this.env.services.action.doAction("gts_chatroom.action_chatroom_resueltas"),
                label: _t("Histórico de resueltas: buscar por nombre o teléfono"),
                icon: "fa fa-history",
            });
        }
        return actions;
    },
});

patch(DiscussSidebarCategories.prototype, {
    setup() {
        super.setup(...arguments);
        // Leer el filtro a través de useState suscribe el lateral a él.
        this.gtsFiltro = useState(filtro);
    },
    filteredThreads(threads) {
        const hilos = super.filteredThreads(threads);
        if (!vista.soloChatroom || !this.gtsFiltro.buzon) {
            return hilos;
        }
        return hilos.filter(
            (thread) => thread.channel_type !== "gateway" || delBuzon(thread)
        );
    },
});

patch(DiscussClientAction.prototype, {
    setup() {
        super.setup(...arguments);
        // Antes de pintar: el lateral se dibuja ya filtrado.
        vista.soloChatroom = Boolean(this.props.action?.context?.gts_chatroom);
    },
    async restoreDiscussThread(props) {
        if (!vista.soloChatroom) {
            return super.restoreDiscussThread(props);
        }
        // EN EL CHATROOM SOLO SE ABRE UNA CONVERSACIÓN DEL CHATROOM (David,
        // 26-11-2026): a José, recién dado de alta, le salía al entrar la
        // última conversación interna de Odoo o el chat de bienvenida de
        // OdooBot. Se abre la que pide el enlace (una tarjeta, un aviso) o la
        // última del ChatRoom; si no hay, la ventana queda en blanco hasta
        // que entre una.
        const explicito =
            props.action.context.active_id ?? props.action.params?.active_id;
        const candidato =
            explicito ||
            this.store.Thread.localIdToActiveId(this.store.discuss.thread?.localId) ||
            this.store.discuss.lastActiveId;
        if (candidato && (explicito || this._gtsEsDelChatroom(candidato))) {
            return super.restoreDiscussThread({
                ...props,
                action: {
                    ...props.action,
                    context: { ...props.action.context, active_id: candidato },
                },
            });
        }
        this.store.discuss.thread = undefined;
        this.store.discuss.hasRestoredThread = true;
    },
    _gtsEsDelChatroom(activeId) {
        const [model, id] = String(activeId).split("_");
        if (model !== "discuss.channel") {
            return false;
        }
        const thread = this.store.Thread.get({ model, id: parseInt(id) });
        return thread?.channel_type === "gateway";
    },
});

// ---------------------------------------------------------------------
// El buzón: nombre, número y el aviso de lo que espera
// ---------------------------------------------------------------------

let audio = null;

function pitar() {
    // Dos notas cortas, sin fichero de sonido. El navegador no deja sonar
    // nada hasta que se ha pulsado algo en la página: el primer pitido puede
    // no oírse.
    try {
        audio = audio || new (window.AudioContext || window.webkitAudioContext)();
        audio.resume?.();
        [0, 0.25].forEach((desfase, n) => {
            const nota = audio.createOscillator();
            const volumen = audio.createGain();
            const inicio = audio.currentTime + desfase;
            nota.frequency.value = n ? 1175 : 880;
            volumen.gain.setValueAtTime(0.0001, inicio);
            volumen.gain.exponentialRampToValueAtTime(0.25, inicio + 0.02);
            volumen.gain.exponentialRampToValueAtTime(0.0001, inicio + 0.2);
            nota.connect(volumen).connect(audio.destination);
            nota.start(inicio);
            nota.stop(inicio + 0.22);
        });
    } catch {
        // Sin audio: queda la bolita.
    }
}

// Cada cuánto pita mientras haya conversaciones sin atender.
const CADA_MS = 20000;

export class GtsBuzon extends Component {
    static template = "gts_chatroom.Buzon";
    static props = {};

    setup() {
        super.setup();
        this.store = useService("mail.store");
        this.orm = useService("orm");
        this.action = useService("action");
        this.filtro = useState(filtro);
        this.estado = useState({
            buzones: [],
            silencio: Boolean(leerNumero(CLAVE_SILENCIO)),
        });
        onWillStart(async () => {
            if (!vista.soloChatroom) {
                return;
            }
            this.estado.buzones = await this.orm.call("gts.chatroom.buzon", "gts_mis_buzones", []);
            // Un buzón guardado que ya no es mío no puede dejar el lateral vacío.
            if (!this.estado.buzones.some((b) => b.id === this.filtro.buzon)) {
                this.filtro.buzon = false;
            }
        });
        onMounted(() => {
            this.reloj = browser.setInterval(() => this.avisar(), CADA_MS);
            this.avisar();
        });
        onWillUnmount(() => browser.clearInterval(this.reloj));
    }

    get visible() {
        return vista.soloChatroom && this.estado.buzones.length > 0;
    }

    get esAdmin() {
        return Boolean(session.is_admin);
    }

    get elegido() {
        return this.estado.buzones.find((b) => b.id === this.filtro.buzon) || false;
    }

    /** Las que esperan, en vivo: del bus, no de la carga. */
    esperandoDe(buzon) {
        return (this.store.discuss.gts_esperando?.threads || []).filter(
            (t) => t.gts_buzon_id === buzon.id
        ).length;
    }

    get pendientes() {
        return (this.store.discuss.gts_esperando?.threads || []).filter(delBuzon).length;
    }

    avisar() {
        if (this.visible && this.pendientes && !this.estado.silencio) {
            pitar();
        }
    }

    elegir(id) {
        this.filtro.buzon = id || false;
        try {
            browser.localStorage.setItem(CLAVE_BUZON, this.filtro.buzon || "");
        } catch {
            // Sin almacenamiento: vale para esta pestaña.
        }
    }

    silenciar() {
        this.estado.silencio = !this.estado.silencio;
        try {
            browser.localStorage.setItem(CLAVE_SILENCIO, this.estado.silencio ? "1" : "");
        } catch {
            // Sin almacenamiento: vale para esta pestaña.
        }
        if (!this.estado.silencio) {
            pitar();
        }
    }

    abrirPendiente() {
        const thread = (this.store.discuss.gts_esperando?.threads || []).find(delBuzon);
        thread?.setAsDiscussThread();
    }

    nuevoBuzon() {
        this.action.doAction("gts_chatroom.action_chatroom_buzones");
    }
}

/** La lista de buzones, debajo de los apartados, como en Superchat. */
export class GtsBuzones extends GtsBuzon {
    static template = "gts_chatroom.Buzones";

    avisar() {
        // El pitido lo da la cabecera; aquí, una sola vez no dos.
    }
}

discussSidebarItemsRegistry.add("gts_buzon", GtsBuzon, { sequence: 15 });
discussSidebarItemsRegistry.add("gts_buzones", GtsBuzones, { sequence: 35 });

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
    .add("gts-descartar", {
        // Una conversación abierta desde «Nueva conversación» a la que no se
        // llegó a escribir: el portal aún no la conoce (sin cola).
        condition: ({ owner, thread }) =>
            thread?.model === "discuss.channel" &&
            thread.channel_type === "gateway" &&
            !thread.gts_cola &&
            vista.soloChatroom &&
            (!owner.props.chatWindow || owner.props.chatWindow.isOpen),
        icon: "fa fa-fw fa-trash-o",
        iconLarge: "fa fa-fw fa-lg fa-trash-o",
        name: _t("Descartar (no se le ha escrito nada)"),
        async open({ owner, thread }) {
            await owner.env.services.orm.call("discuss.channel", "action_gts_descartar", [
                [thread.id],
            ]);
            thread.store.discuss.thread = undefined;
            thread.delete();
        },
        sequence: 9,
    })
    .add("gts-pasar", {
        condition: ({ owner, thread }) =>
            esDelPortal(owner, thread) && thread.gts_cola !== "resuelta",
        // Persona con flecha: «transferir a un compañero», como en las
        // centralitas.
        icon: "fa fa-fw fa-user-plus",
        iconLarge: "fa fa-fw fa-lg fa-user-plus",
        name: _t("Pasar a otro operador"),
        async open({ owner, thread }) {
            const accion = await owner.env.services.orm.call(
                "discuss.channel",
                "action_gts_pasar",
                [[thread.id]]
            );
            await owner.env.services.action.doAction(accion);
        },
        sequence: 8,
    })
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
