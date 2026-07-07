/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { BarcodeHandlerField } from "@barcodes/barcode_handler_field";

patch(BarcodeHandlerField.prototype, {
    onBarcodeScanned(event) {
        super.onBarcodeScanned(...arguments);
    },
});
