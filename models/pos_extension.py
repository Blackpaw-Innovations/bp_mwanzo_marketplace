from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosConfig(models.Model):
    _inherit = "pos.config"

    mwanzo_theme_id = fields.Many2one("mwanzo.market.theme", string="Mwanzo Theme")
    mwanzo_disallow_cash = fields.Boolean(
        string="Disallow Cash Payments (Mwanzo)",
        help="When enabled, cash payments cannot be used in this POS for Mwanzo consignment sales.",
    )


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _process_payment_lines(self, *args, **kwargs):
        # Robust handling for varying signatures across Odoo versions
        order = next((arg for arg in args if isinstance(arg, dict)), kwargs.get('order'))
        pos_session = next((arg for arg in args if hasattr(arg, 'config_id') and arg._name == 'pos.session'), kwargs.get('pos_session'))

        if order and pos_session and pos_session.config_id.mwanzo_disallow_cash:
            for payment in order.get("statement_ids", []):
                payment_vals = payment[2] if len(payment) > 2 else {}
                payment_method = self.env["pos.payment.method"].browse(
                    payment_vals.get("payment_method_id")
                )
                if payment_method and payment_method.journal_id.type == "cash":
                    raise UserError(
                        _("Cash payments are not allowed in this POS configuration.")
                    )
        return super()._process_payment_lines(*args, **kwargs)

    @api.model
    def _order_line_fields(self, line, session_id=None):
        res = super()._order_line_fields(line, session_id=session_id)
        if isinstance(res, (list, tuple)) and len(res) >= 3:
            vals = res[2]
            product = self.env["product.product"].browse(vals.get("product_id"))
            if product:
                vals["mwanzo_vendor_id"] = product.mwanzo_vendor_id.id
                vals["mwanzo_theme_id"] = product.mwanzo_theme_id.id
                vals["mwanzo_commission_percentage"] = (
                    product._get_mwanzo_commission_percentage()
                )
        return res


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    mwanzo_vendor_id = fields.Many2one("res.partner")
    mwanzo_theme_id = fields.Many2one("mwanzo.market.theme")
    mwanzo_commission_percentage = fields.Float(digits=(16, 2))

    @api.model
    def create(self, vals_list):
        if isinstance(vals_list, dict):
            vals_list = [vals_list]
            
        # Pre-fetch products
        products = self.env["product.product"].browse(
            [vals.get("product_id") for vals in vals_list if vals.get("product_id")]
        )
        product_map = {p.id: p for p in products}
        
        # Pre-fetch orders to get sessions and themes
        # Note: When creating lines via order creation, order_id is in vals
        order_ids = [vals.get("order_id") for vals in vals_list if vals.get("order_id")]
        orders = self.env["pos.order"].browse(order_ids)
        order_map = {o.id: o for o in orders}

        for vals in vals_list:
            product = product_map.get(vals.get("product_id"))
            order = order_map.get(vals.get("order_id"))
            
            if product:
                vals.setdefault("mwanzo_vendor_id", product.mwanzo_vendor_id.id)
                vals.setdefault(
                    "mwanzo_commission_percentage",
                    product._get_mwanzo_commission_percentage(),
                )
                
                # Determine Theme from POS Session
                theme = False
                if order and order.session_id.config_id.mwanzo_theme_id:
                    theme = order.session_id.config_id.mwanzo_theme_id
                
                if theme:
                    vals["mwanzo_theme_id"] = theme.id
                    
                    # Validate Vendor License for this Theme
                    if product.mwanzo_vendor_id:
                        license_exists = self.env["mwanzo.vendor.license"].search_count([
                            ("vendor_id", "=", product.mwanzo_vendor_id.id),
                            ("theme_ids", "in", theme.id),
                            ("state", "=", "active"),
                        ])
                        if not license_exists:
                            raise UserError(
                                _(
                                    "Vendor '%s' does not have an active license for theme '%s'. "
                                    "Cannot sell product '%s'."
                                ) % (product.mwanzo_vendor_id.name, theme.name, product.name)
                            )
                else:
                    # Fallback to product theme (legacy behavior)
                    vals.setdefault("mwanzo_theme_id", product.mwanzo_theme_id.id)

        lines = super().create(vals_list)
        return lines
