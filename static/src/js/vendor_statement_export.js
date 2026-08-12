/** @odoo-module **/

import { download } from "@web/core/network/download";
import { useService } from "@web/core/utils/hooks";
import { ExportDataDialog } from "@web/views/view_dialogs/export_data_dialog";
import { Component, onMounted, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";

class VendorStatementExportAction extends Component {
    setup() {
        this.actionService = useService("action");
        this.dialog = useService("dialog");
        this.rpc = useService("rpc");
        this.defaultExportList = [
            { name: "statement_name" },
            { name: "vendor_id" },
            { name: "date_from" },
            { name: "date_to" },
            { name: "pos_order_line_id" },
            { name: "product_id" },
            { name: "theme_id" },
            { name: "quantity" },
            { name: "quantity_remaining" },
            { name: "collected_amount" },
            { name: "sale_amount" },
            { name: "vat_rate" },
            { name: "vat_amount" },
            { name: "commission_percentage" },
            { name: "commission_amount" },
            { name: "discount_amount" },
            { name: "net_amount" },
        ];

        onMounted(() => {
            const context = this.props.action?.context || {};
            const activeIds = context.active_ids || (context.active_id ? [context.active_id] : []);
            const resModel = "mwanzo.vendor.statement.line";
            const domain = [["statement_id", "in", activeIds]];

            this.dialog.add(ExportDataDialog, {
                context,
                defaultExportList: this.defaultExportList,
                download: async (fields, importCompat, format) => {
                    const exportedFields = fields.map((field) => ({
                        name: field.name || field.id,
                        label: field.label || field.string,
                        store: field.store,
                        type: field.field_type || field.type,
                    }));
                    if (importCompat) {
                        exportedFields.unshift({
                            name: "id",
                            label: "External ID",
                        });
                    }
                    await download({
                        data: {
                            data: JSON.stringify({
                                import_compat: importCompat,
                                context,
                                domain,
                                fields: exportedFields,
                                groupby: [],
                                ids: false,
                                model: resModel,
                            }),
                        },
                        url: `/web/export/${format}`,
                    });
                },
                getExportedFields: async (model, importCompat, parentParams) => {
                    return await this.rpc("/web/export/get_fields", {
                        ...parentParams,
                        model,
                        import_compat: importCompat,
                    });
                },
                root: {
                    resModel,
                    domain,
                    groupBy: [],
                    selection: [],
                    isDomainSelected: true,
                },
            });

            this.actionService.doAction({ type: "ir.actions.act_window_close" });
        });
    }
}

VendorStatementExportAction.template = xml`<div class="o_bp_mwanzo_vendor_statement_export_action"/>`;

registry.category("actions").add("bp_mwanzo_vendor_statement_export", VendorStatementExportAction);
