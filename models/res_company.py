from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    mwanzo_store_location_id = fields.Many2one(
        "stock.location",
        string="Mwanzo Store Location",
        help="Destination location for vendor consignment intake.",
    )
    mwanzo_license_product_id = fields.Many2one(
        "product.product",
        string="License Fee Product",
        help="Product used for generating license fee invoices.",
    )

    # Default POS Configuration for Themes
    mwanzo_pos_picking_type_id = fields.Many2one("stock.picking.type", string="Operation Type", domain=[("code", "=", "outgoing")])
    mwanzo_pos_journal_id = fields.Many2one("account.journal", string="Sales Journal", domain=[("type", "=", "sale")])
    mwanzo_pos_invoice_journal_id = fields.Many2one("account.journal", string="Invoice Journal", domain=[("type", "=", "sale")])
    mwanzo_pos_payment_method_ids = fields.Many2many("pos.payment.method", string="Payment Methods")
    mwanzo_vendor_bill_journal_id = fields.Many2one(
        "account.journal",
        string="Vendor Settlement Bill Journal",
        domain=[("type", "=", "purchase")],
    )
    mwanzo_commission_invoice_journal_id = fields.Many2one(
        "account.journal",
        string="Commission Invoice Journal",
        domain=[("type", "=", "sale")],
    )
    mwanzo_commission_clearing_journal_id = fields.Many2one(
        "account.journal",
        string="Commission Clearing Journal",
        domain=[("type", "=", "general")],
    )
    mwanzo_vendor_payout_account_id = fields.Many2one(
        "account.account",
        string="Vendor Payout Expense Account",
        domain=[("account_type", "=", "expense"), ("deprecated", "=", False)],
    )
    mwanzo_commission_income_account_id = fields.Many2one(
        "account.account",
        string="Commission Income Account",
        domain=[("account_type", "in", ("income", "income_other")), ("deprecated", "=", False)],
    )
