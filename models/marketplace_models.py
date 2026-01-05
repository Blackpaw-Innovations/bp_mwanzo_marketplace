"""Core Market Hub marketplace models and partner/event extensions.

Test Plan (manual):
- Create Theme & Space: go to Market Hub > Configuration, create a Theme and a Market Space.
- Create Vendor & License: flag a partner as Mwanzo Vendor, create a Vendor License linked to theme/space.
- Stock Intake: create an intake session under Operations, add lines, validate to generate stock moves with owner.
- POS Sale: sell a product with Mwanzo vendor/theme; verify POS line stores vendor/theme/commission.
- Settlement: run Vendor Settlement Wizard (Accounting) for a date range; open resulting Vendor Statements.
- Staff Commissions: ensure POS orders have staff set; let cron create ledger entries for prior day totals.
"""

import logging
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError, UserError
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    mwanzo_license_id = fields.Many2one("mwanzo.vendor.license", string="Mwanzo License")


class MwanzoThemeStage(models.Model):
    _name = "mwanzo.theme.stage"
    _description = "Theme Stage"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=1)
    fold = fields.Boolean(string="Folded in Kanban")
    target_state = fields.Selection(
        [("draft", "Draft"), ("active", "Active"), ("closed", "Closed")],
        string="Target State",
        help="If set, changing to this stage will automatically set the theme state.",
    )


class MwanzoMarketTheme(models.Model):
    _name = "mwanzo.market.theme"
    _description = "Market Hub Theme"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=True, tracking=True)
    date_start = fields.Date(required=True, tracking=True)
    date_end = fields.Date(required=True, tracking=True)
    description = fields.Text()
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    location_id = fields.Many2one(
        "stock.location",
        string="Theme Location",
        readonly=True,
        copy=False,
        help="Internal stock location created for this theme to hold theme-specific stock.",
    )
    vendor_ids = fields.Many2many(
        "mwanzo.vendor.license",
        relation="mwanzo_theme_license_rel",
        column1="theme_id",
        column2="license_id",
        string="Vendor Licenses",
    )
    event_ids = fields.One2many(
        "event.event",
        "mwanzo_theme_id",
        string="Events",
    )
    event_count = fields.Integer(compute="_compute_event_count")
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("closed", "Closed"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    stage_id = fields.Many2one(
        "mwanzo.theme.stage",
        string="Stage",
        group_expand="_read_group_stage_ids",
        copy=False,
        index=True,
        tracking=True,
    )

    # POS Configuration
    pos_config_id = fields.Many2one("pos.config", string="POS Shop", readonly=True)
    pos_sales_associate_ids = fields.Many2many("hr.employee", "mwanzo_theme_sales_associate_rel", "theme_id", "employee_id", string="Sales Associates")
    pos_cashier_ids = fields.Many2many("hr.employee", "mwanzo_theme_cashier_rel", "theme_id", "employee_id", string="Cashiers")

    _sql_constraints = [
        ("theme_code_unique", "unique(code)", "Theme code must be unique."),
    ]

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        return self.env['mwanzo.theme.stage'].search([], order=order)

    def action_set_active(self):
        stage = self.env['mwanzo.theme.stage'].search([('target_state', '=', 'active')], limit=1)
        vals = {'state': 'active'}
        if stage:
            vals['stage_id'] = stage.id
        self.write(vals)

    def action_set_closed(self):
        stage = self.env['mwanzo.theme.stage'].search([('target_state', '=', 'closed')], limit=1)
        vals = {'state': 'closed'}
        if stage:
            vals['stage_id'] = stage.id
        self.write(vals)

    def action_reset_draft(self):
        stage = self.env['mwanzo.theme.stage'].search([('target_state', '=', 'draft')], order='sequence asc', limit=1)
        vals = {'state': 'draft'}
        if stage:
            vals['stage_id'] = stage.id
        self.write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        themes = super().create(vals_list)
        for theme in themes:
            if theme.state == 'active':
                theme._create_or_update_pos_shop()
                theme._create_or_update_location()
        return themes

    def write(self, vals):
        if 'stage_id' in vals:
            stage = self.env['mwanzo.theme.stage'].browse(vals['stage_id'])
            if stage.target_state:
                vals['state'] = stage.target_state

        res = super().write(vals)
        for theme in self:
            if 'state' in vals:
                if theme.state == 'active':
                    theme._create_or_update_pos_shop()
                    theme._create_or_update_location()
                else:
                    theme._archive_pos_shop()
                    theme._archive_location()

            # Update POS if relevant fields changed and POS exists
            if theme.state == 'active' and theme.pos_config_id and any(f in vals for f in ['name', 'pos_sales_associate_ids', 'pos_cashier_ids']):
                theme._create_or_update_pos_shop()
            # Keep location name in sync
            if theme.location_id and 'name' in vals:
                theme.location_id.write({'name': theme.name})
            if theme.state == 'active' and not theme.location_id:
                theme._create_or_update_location()
        return res

    def _create_or_update_pos_shop(self):
        self.ensure_one()
        if self.state != 'active':
            return

        company = self.env.company
        vals = {
            'name': f"{self.name} Shop",
            'module_pos_hr': True,
            'basic_employee_ids': [(6, 0, (self.pos_sales_associate_ids | self.pos_cashier_ids).ids)],
            'mwanzo_theme_id': self.id,
        }
        
        # Use company defaults
        if company.mwanzo_pos_payment_method_ids:
            vals['payment_method_ids'] = [(6, 0, company.mwanzo_pos_payment_method_ids.ids)]
        if company.mwanzo_pos_picking_type_id:
            vals['picking_type_id'] = company.mwanzo_pos_picking_type_id.id
        if company.mwanzo_pos_journal_id:
            vals['journal_id'] = company.mwanzo_pos_journal_id.id
        if company.mwanzo_pos_invoice_journal_id:
            vals['invoice_journal_id'] = company.mwanzo_pos_invoice_journal_id.id

        if self.pos_config_id:
            self.pos_config_id.write(vals)
        else:
            # Ensure required fields are present for creation
            if company.mwanzo_pos_payment_method_ids and company.mwanzo_pos_picking_type_id and company.mwanzo_pos_journal_id:
                 pos_config = self.env['pos.config'].create(vals)
                 self.pos_config_id = pos_config.id

    def _archive_pos_shop(self):
        self.ensure_one()
        if self.pos_config_id:
            self.pos_config_id.active = False
    def _create_or_update_location(self):
        self.ensure_one()
        if self.state != 'active':
            return

        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company_id.id)], limit=1
        )
        if not warehouse:
            _logger.warning(
                "No warehouse found for company %s; skipping theme location for %s",
                self.company_id.display_name,
                self.name,
            )
            return

        vals = {
            "name": self.name,
            "usage": "internal",
            "location_id": warehouse.lot_stock_id.id,
            "company_id": self.company_id.id,
            "active": True,
        }

        if self.location_id:
            self.location_id.write({"name": self.name, "active": True})
        else:
            self.location_id = self.env["stock.location"].create(vals)

    def _archive_location(self):
        self.ensure_one()
        if self.location_id:
            self.location_id.write({"active": False})

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for record in self:
            if record.date_end and record.date_start and record.date_end < record.date_start:
                raise ValidationError(_("End date must be after start date."))

    def _compute_event_count(self):
        for record in self:
            record.event_count = len(record.event_ids)


class MwanzoMarketSpaceStage(models.Model):
    _name = "mwanzo.market.space.stage"
    _description = "Market Space Stage"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=1)
    fold = fields.Boolean(string="Folded in Kanban")
    active = fields.Boolean(default=True)


class MwanzoMarketSpace(models.Model):
    _name = "mwanzo.market.space"
    _description = "Market Hub Space"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=True)
    space_type = fields.Selection(
        [
            ("kiosk", "Kiosk"),
            ("popup", "Popup"),
            ("rack", "Rack"),
            ("stall", "Stall"),
            ("table", "Table"),
            ("shelf", "Shelf"),
        ],
        string="Space Type",
    )
    capacity_notes = fields.Text()
    theme_id = fields.Many2one("mwanzo.market.theme")
    active = fields.Boolean(default=True)
    stage_id = fields.Many2one(
        "mwanzo.market.space.stage",
        string="Stage",
        group_expand="_read_group_stage_ids",
        copy=False,
        index=True,
        tracking=True,
    )

    _sql_constraints = [
        ("space_code_unique", "unique(code)", "Space code must be unique."),
    ]

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        return self.env['mwanzo.market.space.stage'].search([], order=order)


class MwanzoLicenseStage(models.Model):
    _name = "mwanzo.license.stage"
    _description = "Mwanzo License Stage"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=1)
    fold = fields.Boolean(string="Folded in Kanban")
    active = fields.Boolean(default=True)
    target_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("expired", "Expired"),
            ("cancelled", "Cancelled"),
        ],
        string="Related State",
        help="If set, moving a license to this stage will automatically set its state.",
    )


class MwanzoVendorLicense(models.Model):
    _name = "mwanzo.vendor.license"
    _description = "Mwanzo Vendor License"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(compute="_compute_name", store=True)
    license_number = fields.Char(string="License Number", readonly=True, copy=False, default=lambda self: _('New'))
    vendor_id = fields.Many2one(
        "res.partner",
        required=True,
    )
    is_medium_large = fields.Boolean(string="Medium / Large Scale Vendor?")
    license_type = fields.Selection(
        [
            ("daily", "Daily"),
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
            ("yearly", "Yearly"),
        ],
        required=True,
    )
    date_start = fields.Date(required=True)
    date_end = fields.Date(required=True)
    theme_ids = fields.Many2many("mwanzo.market.theme", string="Themes")
    space_ids = fields.Many2many("mwanzo.market.space", string="Spaces")
    license_fee = fields.Monetary(required=True)
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("expired", "Expired"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    invoice_id = fields.Many2one("account.move", string="Latest Invoice")
    invoice_ids = fields.One2many("account.move", "mwanzo_license_id", string="Invoices")
    billing_method = fields.Selection(
        [("one_time", "One-time (Lump Sum)"), ("monthly", "Monthly Installments")],
        default="one_time",
        required=True,
        string="Billing Method",
    )
    recurring_amount = fields.Monetary(string="Monthly Amount")
    next_invoice_date = fields.Date(string="Next Invoice Date")
    amount_invoiced = fields.Monetary(compute="_compute_amount_invoiced", store=True)
    notes = fields.Text()
    days_to_expire = fields.Integer(compute="_compute_days_to_expire", string="Days to Expire")
    is_expiring_soon = fields.Boolean(compute="_compute_days_to_expire", string="Expiring Soon")
    stage_id = fields.Many2one(
        "mwanzo.license.stage",
        string="Stage",
        group_expand="_read_group_stage_ids",
        copy=False,
        index=True,
        default=lambda self: self.env.ref("bp_mwanzo_marketplace.stage_draft", raise_if_not_found=False),
        tracking=True,
    )
    signed_contract = fields.Binary(string="Signed Contract", attachment=True)
    stock_intake_count = fields.Integer(compute="_compute_counts")
    vendor_statement_count = fields.Integer(compute="_compute_counts")

    def _compute_counts(self):
        for record in self:
            record.stock_intake_count = self.env['mwanzo.stock.intake.session'].search_count([
                ('vendor_id', '=', record.vendor_id.id),
                ('theme_id', 'in', record.theme_ids.ids)
            ])
            record.vendor_statement_count = self.env['mwanzo.vendor.statement'].search_count([
                ('vendor_id', '=', record.vendor_id.id)
            ])

    def action_view_stock_intakes(self):
        self.ensure_one()
        return {
            'name': _('Stock Intakes'),
            'type': 'ir.actions.act_window',
            'res_model': 'mwanzo.stock.intake.session',
            'view_mode': 'tree,form',
            'domain': [('vendor_id', '=', self.vendor_id.id), ('theme_id', 'in', self.theme_ids.ids)],
            'context': {'default_vendor_id': self.vendor_id.id},
        }

    def action_view_vendor_statements(self):
        self.ensure_one()
        return {
            'name': _('Vendor Statements'),
            'type': 'ir.actions.act_window',
            'res_model': 'mwanzo.vendor.statement',
            'view_mode': 'tree,form',
            'domain': [('vendor_id', '=', self.vendor_id.id)],
            'context': {'default_vendor_id': self.vendor_id.id},
        }

    @api.depends("date_end", "state")
    def _compute_days_to_expire(self):
        today = fields.Date.context_today(self)
        for record in self:
            if record.date_end and record.state == 'active':
                delta = (record.date_end - today).days
                record.days_to_expire = delta
                record.is_expiring_soon = 0 <= delta < 30
            else:
                record.days_to_expire = 0
                record.is_expiring_soon = False

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        return self.env['mwanzo.license.stage'].search([], order=order)

    @api.onchange("billing_method", "license_fee", "date_start", "date_end")
    def _onchange_calculate_monthly_amount(self):
        for record in self:
            if record.billing_method == "monthly" and record.license_fee > 0 and record.date_start and record.date_end:
                # Calculate number of months
                r = relativedelta(record.date_end + relativedelta(days=1), record.date_start)
                months = r.years * 12 + r.months
                if months > 0:
                    record.recurring_amount = record.license_fee / months
                else:
                    record.recurring_amount = record.license_fee # Fallback if less than a month?
            elif record.billing_method == "one_time":
                record.recurring_amount = 0.0

    @api.depends("invoice_ids.state", "invoice_ids.amount_total")
    def _compute_amount_invoiced(self):
        for record in self:
            # Sum valid invoices (not cancelled)
            invoices = record.invoice_ids.filtered(lambda inv: inv.state != 'cancel')
            record.amount_invoiced = sum(invoices.mapped('amount_total'))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.vendor_id and not record.vendor_id.is_mwanzo_vendor:
                record.vendor_id.is_mwanzo_vendor = True
        return records

    def write(self, vals):
        if 'stage_id' in vals:
            stage = self.env['mwanzo.license.stage'].browse(vals['stage_id'])
            if stage.target_state:
                vals['state'] = stage.target_state

        res = super().write(vals)
        if "vendor_id" in vals:
            for record in self:
                if record.vendor_id and not record.vendor_id.is_mwanzo_vendor:
                    record.vendor_id.is_mwanzo_vendor = True
        
        if vals.get('state') == 'active':
            for record in self:
                if not record.license_number or record.license_number == _('New'):
                    record.license_number = self.env['ir.sequence'].next_by_code('mwanzo.vendor.license')
        return res

    @api.depends("vendor_id", "theme_ids", "date_start", "date_end")
    def _compute_name(self):
        for record in self:
            vendor = record.vendor_id.display_name or _("Vendor")
            themes = ", ".join(record.theme_ids.mapped("name")) or _("No Theme")
            start = record.date_start or ""
            end = record.date_end or ""
            record.name = f"{vendor} / {themes} / {start}-{end}"

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for record in self:
            if record.date_end and record.date_start and record.date_end < record.date_start:
                raise ValidationError(_("End date must be after start date."))

    def action_set_active(self):
        stage_active = self.env.ref("bp_mwanzo_marketplace.stage_active", raise_if_not_found=False)
        for record in self:
            record.state = "active"
            if stage_active:
                record.stage_id = stage_active
            if record.billing_method == 'monthly' and not record.next_invoice_date:
                record.next_invoice_date = record.date_start

    def action_set_cancelled(self):
        stage_cancelled = self.env.ref("bp_mwanzo_marketplace.stage_cancelled", raise_if_not_found=False)
        for record in self:
            record.state = "cancelled"
            if stage_cancelled:
                record.stage_id = stage_cancelled

    def action_set_expired(self):
        stage_expired = self.env.ref("bp_mwanzo_marketplace.stage_expired", raise_if_not_found=False)
        for record in self:
            record.state = "expired"
            if stage_expired:
                record.stage_id = stage_expired

    def action_reset_to_draft(self):
        stage_draft = self.env.ref("bp_mwanzo_marketplace.stage_draft", raise_if_not_found=False)
        for record in self:
            record.state = "draft"
            if stage_draft:
                record.stage_id = stage_draft

    def action_create_license_invoice(self):
        self.ensure_one()
        if not self.vendor_id:
            raise UserError(_("Please select a vendor before creating the invoice."))
        
        amount_to_invoice = 0.0
        is_recurring = False

        if self.billing_method == 'one_time':
            if self.invoice_id:
                raise UserError(_("An invoice is already linked to this license."))
            amount_to_invoice = self.license_fee
        else:
            # Monthly billing
            if self.amount_invoiced >= self.license_fee:
                raise UserError(_("The full license fee has already been invoiced."))
            
            remaining = self.license_fee - self.amount_invoiced
            amount_to_invoice = min(self.recurring_amount, remaining)
            if amount_to_invoice <= 0:
                raise UserError(_("No amount left to invoice."))
            is_recurring = True

        journal = self.env["account.journal"].search(
            [
                ("type", "=", "sale"),
                ("company_id", "=", self.company_id.id),
            ],
            limit=1,
        )
        if not journal:
            raise UserError(_("Please configure a Sales journal for the company."))

        product = self.company_id.mwanzo_license_product_id
        account_id = False

        if product:
            account_id = product.property_account_income_id.id or product.categ_id.property_account_income_categ_id.id
        
        if not account_id:
            income_account = self.env["account.account"].search(
                [
                    ("account_type", "=", "income"),
                    ("company_id", "=", self.company_id.id),
                    ("deprecated", "=", False),
                ],
                limit=1,
            )
            if not income_account:
                raise UserError(_("Please configure an income account for the company or set a product with an income account."))
            account_id = income_account.id

        invoice_date = fields.Date.context_today(self)
        if is_recurring and self.next_invoice_date:
            invoice_date = self.next_invoice_date

        description = self.name or _("License Fee")
        if is_recurring:
            description += _(" - Installment for %s") % invoice_date.strftime('%B %Y')

        line_vals = {
            "name": description,
            "quantity": 1.0,
            "price_unit": amount_to_invoice,
            "account_id": account_id,
        }
        if product:
            line_vals["product_id"] = product.id

        move_vals = {
            "move_type": "out_invoice",
            "partner_id": self.vendor_id.id,
            "invoice_date": invoice_date,
            "journal_id": journal.id,
            "company_id": self.company_id.id,
            "invoice_origin": self.name,
            "mwanzo_license_id": self.id,
            "invoice_line_ids": [
                (
                    0,
                    0,
                    line_vals,
                )
            ],
        }
        move = self.env["account.move"].create(move_vals)
        self.invoice_id = move.id # Keep track of latest
        
        if is_recurring:
            # Update next invoice date
            next_date = invoice_date + relativedelta(months=1)
            self.next_invoice_date = next_date

        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": move.id,
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            "name": _("Invoices"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "tree,form",
            "domain": [("id", "in", self.invoice_ids.ids)],
            "context": {"default_move_type": "out_invoice", "default_partner_id": self.vendor_id.id},
        }

    @api.model
    def _cron_generate_recurring_invoices(self):
        today = fields.Date.context_today(self)
        licenses = self.search([
            ('state', '=', 'active'),
            ('billing_method', '=', 'monthly'),
            ('next_invoice_date', '<=', today),
        ])
        for license in licenses:
            # Loop to catch up if multiple periods are due
            while license.next_invoice_date and license.next_invoice_date <= today and license.amount_invoiced < license.license_fee:
                try:
                    license.action_create_license_invoice()
                    # Commit after each successful invoice to prevent one failure blocking others
                    self.env.cr.commit()
                except Exception as e:
                    self.env.cr.rollback()
                    _logger.error("Failed to generate recurring invoice for license %s: %s", license.name, str(e))
                    break

    @api.model
    def _cron_update_license_states(self):
        today = fields.Date.context_today(self)
        stage_active = self.env.ref("bp_mwanzo_marketplace.stage_active", raise_if_not_found=False)
        stage_expired = self.env.ref("bp_mwanzo_marketplace.stage_expired", raise_if_not_found=False)

        drafts_to_activate = self.search(
            [
                ("state", "=", "draft"),
                ("date_start", "<=", today),
                ("date_end", ">=", today),
            ]
        )
        vals_active = {"state": "active"}
        if stage_active:
            vals_active["stage_id"] = stage_active.id
        drafts_to_activate.write(vals_active)

        to_expire = self.search(
            [
                ("state", "in", ["draft", "active"]),
                ("date_end", "<", today),
            ]
        )
        vals_expired = {"state": "expired"}
        if stage_expired:
            vals_expired["stage_id"] = stage_expired.id
        to_expire.write(vals_expired)


class EventEvent(models.Model):
    _inherit = "event.event"

    mwanzo_theme_id = fields.Many2one("mwanzo.market.theme", string="Marketplace Theme")
    mwanzo_is_market_activation = fields.Boolean(string="Marketplace Activation")


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_mwanzo_vendor = fields.Boolean(string="Mwanzo Vendor")
    mwanzo_license_ids = fields.One2many(
        "mwanzo.vendor.license",
        "vendor_id",
        string="Mwanzo Licenses",
    )


class MwanzoCommissionRule(models.Model):
    _name = "mwanzo.commission.rule"
    _description = "Mwanzo Commission Rule"

    name = fields.Char(required=True)
    product_category_id = fields.Many2one("product.category")
    default_percentage = fields.Float(
        digits=(16, 2),
        help="Commission percentage e.g. 30 for 30%",
    )
    active = fields.Boolean(default=True)
