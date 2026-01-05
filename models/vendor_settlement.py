from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MwanzoSettlementRunStage(models.Model):
    _name = "mwanzo.settlement.run.stage"
    _description = "Settlement Run Stage"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=1)
    fold = fields.Boolean(string="Folded in Kanban")
    active = fields.Boolean(default=True)
    target_state = fields.Selection(
        [("draft", "Draft"), ("closed", "Closed")],
        string="Target State",
        help="If set, changing to this stage will automatically set the run state.",
    )


class MwanzoSettlementRun(models.Model):
    _name = "mwanzo.settlement.run"
    _description = "Settlement Run"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, readonly=True)
    date_start = fields.Date(required=True)
    date_end = fields.Date(required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('closed', 'Closed'),
    ], default='draft', tracking=True)
    stage_id = fields.Many2one(
        "mwanzo.settlement.run.stage",
        string="Stage",
        group_expand="_read_group_stage_ids",
        copy=False,
        index=True,
        tracking=True,
    )
    statement_ids = fields.One2many('mwanzo.vendor.statement', 'settlement_run_id', string='Statements')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        return self.env['mwanzo.settlement.run.stage'].search([], order=order)

    @api.onchange('date_start', 'date_end')
    def _onchange_dates(self):
        if self.date_start and self.date_end:
            self.name = self.date_start.strftime('%B %Y')

    def action_close(self):
        stage = self.env['mwanzo.settlement.run.stage'].search([('target_state', '=', 'closed')], limit=1)
        vals = {'state': 'closed'}
        if stage:
            vals['stage_id'] = stage.id
        self.write(vals)

    def action_draft(self):
        stage = self.env['mwanzo.settlement.run.stage'].search([('target_state', '=', 'draft')], limit=1)
        vals = {'state': 'draft'}
        if stage:
            vals['stage_id'] = stage.id
        self.write(vals)

    def action_open_wizard(self):
        self.ensure_one()
        return {
            'name': _('Generate Statements'),
            'type': 'ir.actions.act_window',
            'res_model': 'mwanzo.vendor.settlement.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_settlement_run_id': self.id,
                'default_date_from': self.date_start,
                'default_date_to': self.date_end,
            }
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                # Default name to Month Year of date_start
                date_start = fields.Date.from_string(vals.get('date_start'))
                if date_start:
                    vals['name'] = date_start.strftime('%B %Y')
        return super().create(vals_list)

    def write(self, vals):
        if 'stage_id' in vals:
            stage = self.env['mwanzo.settlement.run.stage'].browse(vals['stage_id'])
            if stage.target_state:
                vals['state'] = stage.target_state

        if 'date_start' in vals:
            date_start = fields.Date.from_string(vals['date_start'])
            if date_start:
                vals['name'] = date_start.strftime('%B %Y')
        return super().write(vals)


class MwanzoVendorStatementStage(models.Model):
    _name = "mwanzo.vendor.statement.stage"
    _description = "Vendor Statement Stage"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    target_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("invoiced", "Invoiced"),
            ("paid", "Paid"),
        ],
        string="Target State",
        help="If set, moving to this stage will automatically set the state.",
    )
    fold = fields.Boolean(string="Folded in Kanban")
    active = fields.Boolean(default=True)


class MwanzoVendorStatement(models.Model):
    _name = "mwanzo.vendor.statement"
    _description = "Mwanzo Vendor Statement"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        readonly=True,
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code(
            "mwanzo.vendor.statement"
        ),
    )
    vendor_id = fields.Many2one("res.partner", required=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    line_ids = fields.One2many(
        "mwanzo.vendor.statement.line",
        "statement_id",
        string="Lines",
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    total_sales = fields.Monetary(compute="_compute_totals", store=True)
    total_commission = fields.Monetary(compute="_compute_totals", store=True)
    total_net_payable = fields.Monetary(compute="_compute_totals", store=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("invoiced", "Invoiced"),
            ("paid", "Paid"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    stage_id = fields.Many2one(
        "mwanzo.vendor.statement.stage",
        string="Stage",
        group_expand="_read_group_stage_ids",
        tracking=True,
        copy=False,
        index=True,
        default=lambda self: self._default_stage_id(),
    )
    vendor_bill_id = fields.Many2one("account.move", string="Vendor Bill")
    settlement_run_id = fields.Many2one("mwanzo.settlement.run", string="Settlement Run", ondelete="cascade")

    _sql_constraints = [
        ('unique_vendor_per_run', 'unique(vendor_id, settlement_run_id)', 'A vendor can only have one statement per settlement run.')
    ]

    @api.model
    def _default_stage_id(self):
        return self.env["mwanzo.vendor.statement.stage"].search([], limit=1, order="sequence asc")

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        return self.env["mwanzo.vendor.statement.stage"].search([], order=order)

    def write(self, vals):
        if 'stage_id' in vals:
            stage = self.env['mwanzo.vendor.statement.stage'].browse(vals['stage_id'])
            if stage.target_state:
                vals['state'] = stage.target_state
        return super().write(vals)

    def _update_stage_from_state(self):
        for record in self:
            if not record.state:
                continue
            stage = self.env['mwanzo.vendor.statement.stage'].search([('target_state', '=', record.state)], limit=1)
            if stage and stage != record.stage_id:
                record.stage_id = stage

    @api.depends("line_ids.sale_amount", "line_ids.commission_amount", "line_ids.net_amount")
    def _compute_totals(self):
        for statement in self:
            total_sales = sum(statement.line_ids.mapped("sale_amount"))
            total_commission = sum(statement.line_ids.mapped("commission_amount"))
            total_net = sum(statement.line_ids.mapped("net_amount"))
            statement.total_sales = total_sales
            statement.total_commission = total_commission
            statement.total_net_payable = total_net

    def action_confirm(self):
        for rec in self:
            rec.state = "confirmed"
            rec._update_stage_from_state()

    def action_create_vendor_bill(self):
        for statement in self:
            if statement.vendor_bill_id:
                raise UserError(_("A bill already exists for this statement."))
            if statement.total_net_payable <= 0:
                raise UserError(_("Total net payable is zero or negative; cannot create bill."))

            journal = self.env["account.journal"].search(
                [
                    ("type", "=", "purchase"),
                    ("company_id", "=", statement.company_id.id),
                ],
                limit=1,
            )
            if not journal:
                raise UserError(_("Please configure a Purchase journal for the company."))
            
            # Account for Sales Value (Expense/COGS)
            expense_account = statement.vendor_id.property_account_payable_id # Fallback
            # Try to find a specific expense account if possible, or use a default
            expense_account = self.env["account.account"].search(
                [
                    ("account_type", "=", "expense"),
                    ("company_id", "=", statement.company_id.id),
                    ("deprecated", "=", False),
                ],
                limit=1,
            )
            if not expense_account:
                 raise UserError(_("Please configure an expense account for the company."))

            # Account for Commission (Income)
            income_account = self.env["account.account"].search(
                [
                    ("account_type", "=", "income"),
                    ("company_id", "=", statement.company_id.id),
                    ("deprecated", "=", False),
                ],
                limit=1,
            )
            if not income_account:
                raise UserError(_("Please configure an income account for the company."))

            move = self.env["account.move"].create(
                {
                    "move_type": "in_invoice",
                    "partner_id": statement.vendor_id.id,
                    "invoice_date": fields.Date.context_today(self),
                    "journal_id": journal.id,
                    "company_id": statement.company_id.id,
                    "invoice_origin": statement.name,
                    "invoice_line_ids": [
                        (
                            0,
                            0,
                            {
                                "name": _("Sales Value for %s") % statement.name,
                                "quantity": 1.0,
                                "price_unit": statement.total_sales,
                                "account_id": expense_account.id,
                            },
                        ),
                        (
                            0,
                            0,
                            {
                                "name": _("Commission Retained for %s") % statement.name,
                                "quantity": 1.0,
                                "price_unit": -statement.total_commission,
                                "account_id": income_account.id,
                            },
                        ),
                    ],
                }
            )
            statement.vendor_bill_id = move.id
            statement.state = "invoiced"
            statement._update_stage_from_state()
        return True


class MwanzoVendorStatementLine(models.Model):
    _name = "mwanzo.vendor.statement.line"
    _description = "Mwanzo Vendor Statement Line"

    statement_id = fields.Many2one(
        "mwanzo.vendor.statement",
        required=True,
        ondelete="cascade",
    )
    pos_order_line_id = fields.Many2one("pos.order.line", required=True)
    product_id = fields.Many2one(
        "product.product",
        related="pos_order_line_id.product_id",
        store=True,
        readonly=True,
    )
    theme_id = fields.Many2one(
        "mwanzo.market.theme",
        related="pos_order_line_id.mwanzo_theme_id",
        store=True,
        readonly=True,
    )
    commission_percentage = fields.Float()
    sale_amount = fields.Monetary()
    commission_amount = fields.Monetary()
    net_amount = fields.Monetary()
    currency_id = fields.Many2one(
        "res.currency",
        related="statement_id.currency_id",
        store=True,
        readonly=True,
    )


class MwanzoVendorSettlementWizard(models.TransientModel):
    _name = "mwanzo.vendor.settlement.wizard"
    _description = "Mwanzo Vendor Settlement Wizard"

    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    vendor_id = fields.Many2one("res.partner", domain=[("is_mwanzo_vendor", "=", True)])
    theme_ids = fields.Many2many("mwanzo.market.theme", string="Themes")
    settlement_run_id = fields.Many2one("mwanzo.settlement.run", string="Settlement Run")

    def action_generate_statements(self):
        self.ensure_one()
        domain = [
            ("order_id.date_order", ">=", self.date_from),
            ("order_id.date_order", "<=", self.date_to),
            ("mwanzo_vendor_id", "!=", False),
        ]
        if self.vendor_id:
            domain.append(("mwanzo_vendor_id", "=", self.vendor_id.id))
        if self.theme_ids:
            domain.append(("mwanzo_theme_id", "in", self.theme_ids.ids))

        pos_lines = self.env["pos.order.line"].search(domain)
        if not pos_lines:
            raise UserError(_("No POS order lines found for the given filters."))

        vendors = pos_lines.mapped("mwanzo_vendor_id")
        statements = self.env["mwanzo.vendor.statement"]
        
        # If running from a settlement run, check for existing statements
        existing_vendors = set()
        if self.settlement_run_id:
            existing_statements = self.env["mwanzo.vendor.statement"].search([
                ("settlement_run_id", "=", self.settlement_run_id.id)
            ])
            existing_vendors = set(existing_statements.mapped("vendor_id.id"))

        for vendor in vendors:
            if vendor.id in existing_vendors:
                continue # Skip if already exists in this run

            vendor_lines = pos_lines.filtered(lambda l: l.mwanzo_vendor_id == vendor)
            statement_vals = {
                "vendor_id": vendor.id,
                "date_from": self.date_from,
                "date_to": self.date_to,
                "company_id": self.env.company.id,
            }
            if self.settlement_run_id:
                statement_vals["settlement_run_id"] = self.settlement_run_id.id
            
            statement = statements.create(statement_vals)
            line_vals = []
            for line in vendor_lines:
                sale_amount = line.price_subtotal
                commission_percentage = line.mwanzo_commission_percentage or 0.0
                commission_amount = sale_amount * commission_percentage / 100.0
                net_amount = sale_amount - commission_amount
                line_vals.append(
                    (
                        0,
                        0,
                        {
                            "pos_order_line_id": line.id,
                            "commission_percentage": commission_percentage,
                            "sale_amount": sale_amount,
                            "commission_amount": commission_amount,
                            "net_amount": net_amount,
                        },
                    )
                )
            statement.write({"line_ids": line_vals})
            statements |= statement
        
        if self.settlement_run_id:
            return {'type': 'ir.actions.act_window_close'}

        return {
            "type": "ir.actions.act_window",
            "name": _("Vendor Statements"),
            "res_model": "mwanzo.vendor.statement",
            "view_mode": "tree,form",
            "domain": [("id", "in", statements.ids)],
        }
