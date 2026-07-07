from odoo import _, api, fields, models


class MwanzoInventoryScanWizard(models.TransientModel):
    _inherit = "barcodes.barcode_events_mixin"
    _name = "mwanzo.inventory.scan.wizard"
    _description = "Market Hub Inventory Scan"

    location_id = fields.Many2one(
        "stock.location",
        string="Location",
        required=True,
        domain=[("usage", "=", "internal")],
    )
    _barcode_scanned = fields.Char(string="Scanned Barcode")
    line_ids = fields.One2many(
        "mwanzo.inventory.scan.line",
        "wizard_id",
        string="Scanned Items",
    )
    message = fields.Char(string="Message", readonly=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        location = False
        if self.env.context.get("default_location_id"):
            location = self.env["stock.location"].browse(self.env.context["default_location_id"])
        elif self.env.context.get("active_model") == "stock.quant" and self.env.context.get("active_ids"):
            quants = self.env["stock.quant"].browse(self.env.context["active_ids"]).filtered("location_id")
            location = quants[:1].location_id if quants else False
        if not location:
            warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1)
            location = warehouse.lot_stock_id if warehouse else False
        if location:
            values["location_id"] = location.id
        return values

    def _find_product_by_barcode(self, barcode):
        return self.env["product.product"].search([("barcode", "=", barcode)], limit=1)

    def _get_or_create_line(self, product):
        self.ensure_one()
        line = self.line_ids.filtered(lambda l: l.product_id == product and l.location_id == self.location_id)[:1]
        if line:
            return line, False
        self.update(
            {
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "barcode": product.barcode,
                            "location_id": self.location_id.id,
                            "on_hand_qty": self._get_on_hand_qty(product),
                            "counted_qty": 0.0,
                        },
                    )
                ]
            }
        )
        line = self.line_ids.filtered(lambda l: l.product_id == product and l.location_id == self.location_id)[:1]
        return line, True

    def _get_or_create_inventory_quant(self, product):
        self.ensure_one()
        quant = self.env["stock.quant"].search(
            [
                ("product_id", "=", product.id),
                ("location_id", "=", self.location_id.id),
            ],
            order="quantity desc, id asc",
            limit=1,
        ).with_context(inventory_mode=True)
        if quant:
            return quant
        return self.env["stock.quant"].with_context(inventory_mode=True).create(
            {
                "product_id": product.id,
                "location_id": self.location_id.id,
                "inventory_quantity": 0.0,
                "inventory_quantity_set": True,
            }
        )

    def _get_on_hand_qty(self, product):
        return self.env["stock.quant"]._get_available_quantity(
            product,
            self.location_id,
            strict=False,
            allow_negative=True,
        )

    def on_barcode_scanned(self, barcode):
        self.ensure_one()
        barcode = (barcode or "").strip()
        if not barcode:
            self.message = _("Scan a barcode first.")
            return
        product = self._find_product_by_barcode(barcode)
        if not product:
            self.message = _("No product found for barcode %s.") % barcode
            return
        quant = self._get_or_create_inventory_quant(product)
        next_count = (quant.inventory_quantity if quant.inventory_quantity_set else 0.0) + 1.0
        quant.write(
            {
                "inventory_quantity": next_count,
                "inventory_quantity_set": True,
            }
        )
        line, _is_new_line = self._get_or_create_line(product)
        line.counted_qty = quant.inventory_quantity
        line.on_hand_qty = self._get_on_hand_qty(product)
        self.message = _("Added %s.") % product.display_name
        return

    def action_clear(self):
        self.ensure_one()
        quants = self.env["stock.quant"]
        for line in self.line_ids.filtered("product_id"):
            quant = self.env["stock.quant"].search(
                [
                    ("product_id", "=", line.product_id.id),
                    ("location_id", "=", line.location_id.id),
                ],
                order="quantity desc, id asc",
                limit=1,
            ).with_context(inventory_mode=True)
            quants |= quant
        if quants:
            quants.action_clear_inventory_quantity()
        self.line_ids.unlink()
        self._barcode_scanned = False
        self.message = False
        return True

    def action_apply_all(self):
        self.ensure_one()
        self.message = _("Scanned counts saved to inventory lines. Use Apply All to update stock on hand.")
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_save_session(self):
        self.ensure_one()
        empty_lines = self.line_ids.filtered(lambda line: not line.product_id)
        if empty_lines:
            empty_lines.unlink()
        self.message = _("Inventory scan session saved.")
        return {"type": "ir.actions.client", "tag": "reload"}


class MwanzoInventoryScanLine(models.TransientModel):
    _name = "mwanzo.inventory.scan.line"
    _description = "Market Hub Inventory Scan Line"

    wizard_id = fields.Many2one(
        "mwanzo.inventory.scan.wizard",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one("product.product")
    barcode = fields.Char(related="product_id.barcode", readonly=True)
    location_id = fields.Many2one("stock.location", required=True)
    on_hand_qty = fields.Float(string="On Hand Qty", readonly=True)
    counted_qty = fields.Float(string="Counted Qty")
    difference_qty = fields.Float(string="Difference", compute="_compute_difference_qty", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            wizard = self.env["mwanzo.inventory.scan.wizard"].browse(vals.get("wizard_id"))
            if wizard:
                vals.setdefault("location_id", wizard.location_id.id)
        return super().create(vals_list)

    @api.depends("on_hand_qty", "counted_qty")
    def _compute_difference_qty(self):
        for line in self:
            line.difference_qty = line.counted_qty - line.on_hand_qty

    def action_remove_line(self):
        quant = self.env["stock.quant"].search(
            [
                ("product_id", "=", self.product_id.id),
                ("location_id", "=", self.location_id.id),
            ],
            order="quantity desc, id asc",
            limit=1,
        ).with_context(inventory_mode=True)
        if quant:
            quant.action_clear_inventory_quantity()
        self.unlink()
        return True


class StockQuant(models.Model):
    _inherit = "stock.quant"

    def action_open_mwanzo_inventory_scan(self):
        location = self[:1].location_id if self else False
        return {
            "type": "ir.actions.act_window",
            "name": _("Scan Inventory"),
            "res_model": "mwanzo.inventory.scan.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_location_id": location.id if location else False,
                "active_model": "stock.quant",
                "active_ids": self.ids,
            },
        }
