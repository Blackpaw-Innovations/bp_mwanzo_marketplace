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
    def create(self, vals):
        if not vals.get("mwanzo_item_code"):
            vals["mwanzo_item_code"] = self.env["ir.sequence"].next_by_code(
                "mwanzo.item.code"
            )
        return super().create(vals)

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

    def _get_mwanzo_commission_percentage(self):
        self.ensure_one()
        return self.product_tmpl_id._get_mwanzo_commission_percentage()
