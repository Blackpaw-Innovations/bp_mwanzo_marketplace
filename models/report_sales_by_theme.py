from odoo import fields, models, tools


class MwanzoSalesByThemeReport(models.Model):
    _name = "mwanzo.sales.by.theme.report"
    _description = "Sales by Theme Report"
    _auto = False
    _rec_name = "theme_id"

    theme_id = fields.Many2one("mwanzo.market.theme", string="Theme")
    vendor_id = fields.Many2one("res.partner", string="Vendor")
    total_sales = fields.Monetary(string="Total Sales")
    total_commission = fields.Monetary(string="Total Commission")
    currency_id = fields.Many2one("res.currency", string="Currency")

    def init(self):
        tools.drop_view_if_exists(self._cr, self._table)
        self._cr.execute(
            """
            CREATE OR REPLACE VIEW %s AS (
                SELECT
                    MIN(pol.id) AS id,
                    pol.mwanzo_theme_id AS theme_id,
                    pol.mwanzo_vendor_id AS vendor_id,
                    SUM(pol.price_subtotal) AS total_sales,
                    SUM(
                        pol.price_subtotal * COALESCE(pol.mwanzo_commission_percentage, 0) / 100
                    ) AS total_commission,
                    c.currency_id AS currency_id
                FROM pos_order_line pol
                JOIN pos_order po ON pol.order_id = po.id
                JOIN res_company c ON po.company_id = c.id
                WHERE pol.mwanzo_theme_id IS NOT NULL
                    AND pol.mwanzo_vendor_id IS NOT NULL
                GROUP BY pol.mwanzo_theme_id, pol.mwanzo_vendor_id, c.currency_id
            )
            """
            % self._table
        )
