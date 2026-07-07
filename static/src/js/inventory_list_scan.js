/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { session } from "@web/session";
import { InventoryReportListController } from "@stock/views/list/inventory_report_list_controller";
import { InventoryReportListView } from "@stock/views/list/inventory_report_list_view";

InventoryReportListView.buttonTemplate = "bp_mwanzo_marketplace.InventoryReportButtons";

patch(InventoryReportListController.prototype, {
    getStaticActionMenuItems() {
        const items = super.getStaticActionMenuItems(...arguments);
        if (this.props.resModel === "stock.quant" && this.props.context.inventory_mode) {
            items.scan_inventory = {
                sequence: 15,
                icon: "fa fa-qrcode",
                description: _t("Scan Inventory"),
                callback: () => this.onClickScanInventory(),
            };
        }
        return items;
    },

    async onClickScanInventory() {
        const activeIds = await this.model.orm.search(this.props.resModel, this.props.domain, {
            limit: session.active_ids_limit,
            context: this.props.context,
        });
        return this.actionService.doAction("bp_mwanzo_marketplace.action_server_mwanzo_inventory_scan", {
            additionalContext: {
                active_ids: activeIds,
            },
            onClose: () => {
                this.model.load();
            },
        });
    },
});
