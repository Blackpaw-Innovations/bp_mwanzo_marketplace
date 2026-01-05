/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

class MarketHubDashboard extends Component {
    setup() {
        this.action = useService("action");
        this.onMessage = (ev) => {
            if (ev.data && ev.data.type === "open_action") {
                this.action.doAction(ev.data.action, ev.data.options || {});
            }
        };
        onMounted(() => {
            window.addEventListener("message", this.onMessage);
        });
        onWillUnmount(() => {
            window.removeEventListener("message", this.onMessage);
        });
    }
}

MarketHubDashboard.template = "bp_mwanzo_marketplace.MarketHubDashboard";

registry.category("actions").add("mwanzo_dashboard_client_action", MarketHubDashboard);
