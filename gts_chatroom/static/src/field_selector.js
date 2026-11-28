import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { ModelFieldSelector } from "@web/core/model_field_selector/model_field_selector";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { formatChar } from "@web/views/fields/formatters";

/**
 * ELEGIR UN CAMPO DE UNA FICHA («partner_id.mobile») — RAMA 18.
 *
 * En la 19 esto es el widget `field_selector` de Odoo
 * (web/static/src/views/fields/field_selector). En la 18 NO EXISTE: un
 * `widget="field_selector"` en la vista deja la ficha de la plantilla sin
 * abrir («Missing widget»). Aquí va lo mismo, sobre el selector de campos
 * que sí trae la 18 (`ModelFieldSelector`), con otro nombre para no pisar
 * el de Odoo si un día lo añade.
 *
 * Opción `model`: el campo de la ficha que dice de qué modelo se eligen.
 */
export class GtsFieldSelectorField extends Component {
    static template = "gts_chatroom.FieldSelectorField";
    static components = { ModelFieldSelector };
    static props = {
        ...standardFieldProps,
        resModel: { type: String, optional: true },
    };

    filter(fieldDef) {
        return fieldDef.type !== "separator" && fieldDef.type !== "properties";
    }

    async update(value) {
        await this.props.record.update({ [this.props.name]: value });
    }

    get formattedValue() {
        return formatChar(this.props.record.data[this.props.name]);
    }

    get resModel() {
        return this.props.record.data[this.props.resModel] || this.props.record.resModel;
    }

    get selectorProps() {
        return {
            allowEmpty: !this.props.required,
            path: this.props.record.data[this.props.name] || "",
            resModel: this.resModel,
            readonly: this.props.readonly,
            update: this.update.bind(this),
            isDebugMode: !!this.env.debug,
            filter: this.filter.bind(this),
            followRelations: true,
        };
    }
}

registry.category("fields").add("gts_field_selector", {
    component: GtsFieldSelectorField,
    displayName: _t("Selector de campo"),
    supportedTypes: ["char"],
    extractProps({ options }) {
        return { resModel: options.model };
    },
});
