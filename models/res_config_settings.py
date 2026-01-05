from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mwanzo_store_location_id = fields.Many2one(
        related="company_id.mwanzo_store_location_id",
        readonly=False,
        string="Mwanzo Store Location",
    )
    mwanzo_license_product_id = fields.Many2one(
        related="company_id.mwanzo_license_product_id",
        readonly=False,
        string="License Fee Product",
    )

    mwanzo_pos_picking_type_id = fields.Many2one(
        related="company_id.mwanzo_pos_picking_type_id",
        readonly=False,
        string="Default Operation Type",
    )
    mwanzo_pos_journal_id = fields.Many2one(
        related="company_id.mwanzo_pos_journal_id",
        readonly=False,
        string="Default Sales Journal",
    )
    mwanzo_pos_invoice_journal_id = fields.Many2one(
        related="company_id.mwanzo_pos_invoice_journal_id",
        readonly=False,
        string="Default Invoice Journal",
    )
    mwanzo_pos_payment_method_ids = fields.Many2many(
        related="company_id.mwanzo_pos_payment_method_ids",
        readonly=False,
        string="Default Payment Methods",
    )

    module_bp_mwanzo_marketplace_hr = fields.Boolean(
        string="HR & Performance add-on",
        help="Install BP Market Hub HR to enable staff commission menus and scheduler.",
    )
