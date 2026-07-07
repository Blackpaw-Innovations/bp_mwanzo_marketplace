from datetime import datetime

from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    mwanzo_vendor_id = fields.Many2one(
        "res.partner",
        string="Mwanzo Vendor",
        domain=[("is_mwanzo_vendor", "=", True)],
    )
    mwanzo_theme_id = fields.Many2one("mwanzo.market.theme", string="Marketplace Theme")
    mwanzo_commission_rule_id = fields.Many2one(
        "mwanzo.commission.rule",
        string="Commission Rule",
    )
    mwanzo_item_code = fields.Char(
        string="Mwanzo Item Code",
        readonly=True,
        copy=False,
    )
    mwanzo_min_qty = fields.Float(
        string="Minimum Quantity",
        help="Minimum quantity to trigger auto-replenishment.",
        default=0.0,
    )
    mwanzo_replenish_qty = fields.Float(
        string="Replenishment Quantity",
        help="Quantity to add to the intake session when stock is below minimum.",
        default=0.0,
    )

    @api.model
    def _resolve_mwanzo_commission_rule(self, vals):
        rule_value = vals.get("mwanzo_commission_rule_id")
        if not rule_value or isinstance(rule_value, int):
            return vals

        if isinstance(rule_value, str):
            rule_name = rule_value.strip()
            if not rule_name:
                vals["mwanzo_commission_rule_id"] = False
                return vals

            rule = self.env["mwanzo.commission.rule"].search([("name", "=", rule_name)], limit=1)
            if not rule:
                rule = self.env["mwanzo.commission.rule"].name_create(rule_name)
                rule = self.env["mwanzo.commission.rule"].browse(rule[0])
            vals["mwanzo_commission_rule_id"] = rule.id
            return vals

        return vals

    @api.model
    @api.model
    def _compute_ean13_check_digit(self, code12):
        digits = [int(char) for char in code12]
        odd_sum = sum(digits[::2])
        even_sum = sum(digits[1::2])
        total = odd_sum + (even_sum * 3)
        return str((10 - (total % 10)) % 10)

    @api.model
    def _generate_mwanzo_ean13_barcode(self):
        date_prefix = datetime.utcnow().strftime("%y%m%d")
        sequence_value = self.env["ir.sequence"].next_by_code("mwanzo.product.barcode") or "1"
        numeric_part = "".join(char for char in str(sequence_value) if char.isdigit()) or "1"
        serial = f"{int(numeric_part) % 1000000:06d}"
        code12 = f"{date_prefix}{serial}"
        return f"{code12}{self._compute_ean13_check_digit(code12)}"

    def _get_mwanzo_barcode_fallback(self, vals, record=None):
        return self._generate_mwanzo_ean13_barcode()

    @api.model
    def _apply_mwanzo_barcode_fallback(self, vals, record=None):
        barcode_in_vals = "barcode" in vals
        barcode_value = vals.get("barcode")

        # Respect explicit non-empty barcode values.
        if barcode_in_vals and barcode_value:
            return vals

        current_barcode = record.barcode if record else False
        if current_barcode and not (barcode_in_vals and not barcode_value):
            return vals

        fallback = self._get_mwanzo_barcode_fallback(vals, record=record)
        if fallback:
            vals["barcode"] = fallback
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._resolve_mwanzo_commission_rule(vals)
            if not vals.get("mwanzo_item_code"):
                vals["mwanzo_item_code"] = self.env["ir.sequence"].next_by_code(
                    "mwanzo.item.code"
                )
            self._apply_mwanzo_barcode_fallback(vals)
        return super().create(vals_list)

    def write(self, vals):
        for record in self:
            record_vals = dict(vals)
            self._resolve_mwanzo_commission_rule(record_vals)
            self._apply_mwanzo_barcode_fallback(record_vals, record=record)
            super(ProductTemplate, record).write(record_vals)
            empty_variants = record.product_variant_ids.filtered(lambda v: not v.barcode)
            for variant in empty_variants:
                variant.write({"barcode": self._generate_mwanzo_ean13_barcode()})
        return True

    @api.model
    def _cron_backfill_empty_barcodes(self):
        variants = self.env["product.product"].search(
            [
                ("barcode", "=", False),
            ]
        )
        for variant in variants:
            variant.write({"barcode": self._generate_mwanzo_ean13_barcode()})
        return len(variants)

    @api.model
    def action_autogenerate_missing_barcodes(self):
        updated_count = self._cron_backfill_empty_barcodes()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Barcode generation complete",
                "message": f"Updated {updated_count} product barcode(s).",
                "type": "success",
                "sticky": False,
            },
        }

    def _get_mwanzo_commission_percentage(self):
        self.ensure_one()
        if self.mwanzo_commission_rule_id:
            return self.mwanzo_commission_rule_id.default_percentage or 0.0
        if self.categ_id:
            rule = self.env["mwanzo.commission.rule"].search(
                [
                    ("product_category_id", "=", self.categ_id.id),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if rule:
                return rule.default_percentage or 0.0
        return 0.0


class ProductProduct(models.Model):
    _inherit = "product.product"

    mwanzo_vendor_id = fields.Many2one(
        related="product_tmpl_id.mwanzo_vendor_id",
        store=True,
        readonly=False,
    )
    mwanzo_theme_id = fields.Many2one(
        related="product_tmpl_id.mwanzo_theme_id",
        store=True,
        readonly=False,
    )
    mwanzo_commission_rule_id = fields.Many2one(
        related="product_tmpl_id.mwanzo_commission_rule_id",
        store=True,
        readonly=False,
    )
    mwanzo_item_code = fields.Char(
        related="product_tmpl_id.mwanzo_item_code",
        store=True,
        readonly=True,
    )
    mwanzo_min_qty = fields.Float(
        related="product_tmpl_id.mwanzo_min_qty",
        store=True,
        readonly=False,
    )
    mwanzo_replenish_qty = fields.Float(
        related="product_tmpl_id.mwanzo_replenish_qty",
        store=True,
        readonly=False,
    )

    @api.model
    def _apply_variant_mwanzo_barcode_fallback(self, vals, record=None):
        barcode_in_vals = "barcode" in vals
        barcode_value = vals.get("barcode")
        if barcode_in_vals and barcode_value:
            return vals

        current_barcode = record.barcode if record else False
        if current_barcode and not (barcode_in_vals and not barcode_value):
            return vals

        fallback = self.env["product.template"]._generate_mwanzo_ean13_barcode()
        if fallback:
            vals["barcode"] = fallback
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._apply_variant_mwanzo_barcode_fallback(vals)
        return super().create(vals_list)

    def write(self, vals):
        for record in self:
            record_vals = dict(vals)
            self._apply_variant_mwanzo_barcode_fallback(record_vals, record=record)
            super(ProductProduct, record).write(record_vals)
        return True

    def _get_mwanzo_commission_percentage(self):
        self.ensure_one()
        return self.product_tmpl_id._get_mwanzo_commission_percentage()
