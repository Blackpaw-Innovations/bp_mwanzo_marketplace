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
        this.orm = useService("orm");
        this.rpc = useService("rpc");
        this.defaultExportList = [
            { name: "name" },
            { name: "vendor_id" },
            { name: "date_from" },
            { name: "date_to" },
            { name: "total_sales" },
            { name: "total_commission" },
            { name: "total_net_payable" },
            { name: "state" },
        ];

        onMounted(() => {
            const context = this.props.action?.context || {};
            const activeIds = context.active_ids || (context.active_id ? [context.active_id] : []);
            const resModel = context.active_model || "mwanzo.vendor.statement";

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
                                domain: [["id", "in", activeIds]],
                                fields: exportedFields,
                                groupby: [],
                                ids: activeIds,
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
                    domain: [["id", "in", activeIds]],
                    groupBy: [],
                    selection: activeIds,
                    isDomainSelected: false,
                },
            });

            this.actionService.doAction({ type: "ir.actions.act_window_close" });
        });
    }
}

VendorStatementExportAction.template = xml`<div class="o_bp_mwanzo_vendor_statement_export_action"/>`;

registry.category("actions").add("bp_mwanzo_vendor_statement_export", VendorStatementExportAction);
