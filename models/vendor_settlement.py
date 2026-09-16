import base64
import csv
import io
from datetime import timedelta
import xlsxwriter

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

    def action_recompute_statements(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Only draft settlement runs can be recomputed."))
        wizard = self.env["mwanzo.vendor.settlement.wizard"].create({
            "date_from": self.date_start,
            "date_to": self.date_end,
            "settlement_run_id": self.id,
        })
        return wizard.action_generate_statements()

    def action_open_statements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Vendor Statements"),
            "res_model": "mwanzo.vendor.statement",
            "view_mode": "tree,form",
            "domain": [("settlement_run_id", "=", self.id)],
            "context": {"default_settlement_run_id": self.id},
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
        default=lambda self: _("New"),
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
    payout_notes = fields.Text(string="Payout Notes", tracking=True)
    total_collected = fields.Monetary(compute="_compute_totals", store=True)
    total_discount = fields.Monetary(compute="_compute_totals", store=True)
    total_vat = fields.Monetary(compute="_compute_totals", store=True)
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
    commission_invoice_id = fields.Many2one(
        "account.move",
        string="Commission Invoice",
        copy=False,
        readonly=True,
    )
    commission_clearing_move_id = fields.Many2one(
        "account.move",
        string="Commission Clearing Entry",
        copy=False,
        readonly=True,
    )
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
        result = super().write(vals)
        if vals.get("state") in ("confirmed", "invoiced", "paid"):
            self._assign_statement_name()
        return result

    def _assign_statement_name(self):
        for statement in self.filtered(lambda rec: not rec.name or rec.name == _("New")):
            statement.name = self.env["ir.sequence"].next_by_code("mwanzo.vendor.statement") or _("New")

    def _update_stage_from_state(self):
        for record in self:
            if not record.state:
                continue
            stage = self.env['mwanzo.vendor.statement.stage'].search([('target_state', '=', record.state)], limit=1)
            if stage and stage != record.stage_id:
                record.stage_id = stage

    @api.depends(
        "line_ids.collected_amount",
        "line_ids.discount_amount",
        "line_ids.sale_amount",
        "line_ids.vat_amount",
        "line_ids.commission_amount",
        "line_ids.net_amount",
    )
    def _compute_totals(self):
        for statement in self:
            total_collected = sum(statement.line_ids.mapped("collected_amount"))
            total_discount = sum(statement.line_ids.mapped("discount_amount"))
            total_sales = sum(statement.line_ids.mapped("sale_amount"))
            total_vat = sum(statement.line_ids.mapped("vat_amount"))
            total_commission = sum(statement.line_ids.mapped("commission_amount"))
            total_net = sum(statement.line_ids.mapped("net_amount"))
            statement.total_collected = total_collected
            statement.total_discount = total_discount
            statement.total_sales = total_sales
            statement.total_vat = total_vat
            statement.total_commission = total_commission
            statement.total_net_payable = total_net

    def action_confirm(self):
        for rec in self:
            rec._assign_statement_name()
            rec.state = "confirmed"
            rec._update_stage_from_state()

    def action_recompute_lines(self):
        for statement in self:
            if statement.state in ("invoiced", "paid"):
                raise UserError(_("You cannot recompute an invoiced or paid statement. Create an adjustment statement instead."))
            statement._recompute_statement_lines()
            if statement.state == "confirmed":
                statement.state = "draft"
                statement._update_stage_from_state()
        return True

    def _recompute_statement_lines(self):
        self.ensure_one()
        existing_pos_lines = self.line_ids.mapped("pos_order_line_ids") | self.line_ids.mapped("pos_order_line_id")
        existing_pos_lines = existing_pos_lines.filtered(lambda line: line)
        pos_lines = self._get_settlement_pos_lines(
            self.date_from,
            self.date_to,
            vendor=self.vendor_id,
        )
        pos_lines = pos_lines.filtered(
            lambda line: not line.mwanzo_vendor_statement_line_id
            or line.mwanzo_vendor_statement_line_id.statement_id == self
        )
        pos_lines |= existing_pos_lines
        existing_pos_lines.write({"mwanzo_vendor_statement_line_id": False})
        self.line_ids.unlink()
        self._create_grouped_statement_lines(pos_lines)

    @api.model
    def _get_settlement_pos_lines(self, date_from, date_to, vendor=False, themes=False):
        domain = [
            ("order_id.date_order", ">=", date_from),
            ("order_id.date_order", "<", fields.Date.to_date(date_to) + timedelta(days=1)),
            ("order_id.state", "in", ("paid", "done", "invoiced")),
            ("mwanzo_vendor_statement_line_id", "=", False),
        ]
        if themes:
            domain.append(("mwanzo_theme_id", "in", themes.ids))
        pos_lines = self.env["pos.order.line"].search(domain, order="id")
        self._backfill_mwanzo_pos_line_data(pos_lines)
        if vendor:
            pos_lines = pos_lines.filtered(lambda line: line.mwanzo_vendor_id == vendor)
        if themes:
            pos_lines = pos_lines.filtered(lambda line: line.mwanzo_theme_id in themes)
        return pos_lines.filtered("mwanzo_vendor_id")

    @api.model
    def _backfill_mwanzo_pos_line_data(self, pos_lines):
        for line in pos_lines:
            vals = {}
            product = line.product_id
            if not line.mwanzo_vendor_id and product.mwanzo_vendor_id:
                vals["mwanzo_vendor_id"] = product.mwanzo_vendor_id.id
            if not line.mwanzo_theme_id:
                theme = line.order_id.session_id.config_id.mwanzo_theme_id or product.mwanzo_theme_id
                if theme:
                    vals["mwanzo_theme_id"] = theme.id
            if not line.mwanzo_commission_percentage and product:
                vals["mwanzo_commission_percentage"] = product._get_mwanzo_commission_percentage()
            if vals:
                line.write(vals)

    def _create_grouped_statement_lines(self, pos_lines):
        self.ensure_one()
        groups = {}
        for pos_line in pos_lines:
            sale_amount = pos_line.price_subtotal or 0.0
            collected_amount = pos_line.price_subtotal_incl or 0.0
            vat_amount = collected_amount - sale_amount
            vat_rate = (vat_amount / sale_amount * 100.0) if sale_amount else 0.0
            key = (
                pos_line.product_id.id,
                pos_line.mwanzo_theme_id.id or False,
            )
            groups.setdefault(key, self.env["pos.order.line"])
            groups[key] |= pos_line

        for grouped_lines in groups.values():
            first_line = grouped_lines[0]
            sale_amount = sum(grouped_lines.mapped("price_subtotal"))
            collected_amount = sum(grouped_lines.mapped("price_subtotal_incl"))
            vat_amount = collected_amount - sale_amount
            vat_rate = (vat_amount / sale_amount * 100.0) if sale_amount else 0.0
            commission_amount = sum(
                (line.price_subtotal or 0.0) * (line.mwanzo_commission_percentage or 0.0) / 100.0
                for line in grouped_lines
            )
            commission_percentage = (commission_amount / sale_amount * 100.0) if sale_amount else 0.0
            statement_line = self.env["mwanzo.vendor.statement.line"].create({
                "statement_id": self.id,
                "pos_order_line_id": first_line.id,
                "pos_order_line_ids": [(6, 0, grouped_lines.ids)],
                "product_id": first_line.product_id.id,
                "theme_id": first_line.mwanzo_theme_id.id,
                "commission_percentage": commission_percentage,
                "quantity": sum(grouped_lines.mapped("qty")),
                "collected_amount": collected_amount,
                "sale_amount": sale_amount,
                "vat_rate": vat_rate,
                "vat_amount": vat_amount,
                "commission_amount": commission_amount,
                "discount_amount": sum(
                    max((line.price_unit or 0.0) * (line.qty or 0.0) - (line.price_subtotal or 0.0), 0.0)
                    for line in grouped_lines
                ),
                "net_amount": collected_amount - commission_amount,
            })
            grouped_lines.write({"mwanzo_vendor_statement_line_id": statement_line.id})

    def action_create_vendor_bill(self):
        for statement in self:
            if statement.total_net_payable <= 0:
                raise UserError(_("Total net payable is zero or negative; cannot create bill."))

            if not statement.vendor_bill_id:
                statement.vendor_bill_id = statement._create_vendor_payout_bill()
            if statement.total_commission and not statement.commission_invoice_id:
                statement.commission_invoice_id = statement._create_commission_invoice()
            if statement.commission_invoice_id and not statement.commission_clearing_move_id:
                statement.commission_clearing_move_id = statement._create_commission_clearing_entry()

            statement.state = "invoiced"
            statement._update_stage_from_state()
        return True

    def _get_statement_purchase_journal(self):
        self.ensure_one()
        journal = self.company_id.mwanzo_vendor_bill_journal_id or self.env["account.journal"].search(
            [("type", "=", "purchase"), ("company_id", "=", self.company_id.id)],
            limit=1,
        )
        if not journal:
            raise UserError(_("Please configure a purchase journal for vendor settlement bills."))
        return journal

    def _get_statement_sale_journal(self):
        self.ensure_one()
        journal = self.company_id.mwanzo_commission_invoice_journal_id or self.env["account.journal"].search(
            [("type", "=", "sale"), ("company_id", "=", self.company_id.id)],
            limit=1,
        )
        if not journal:
            raise UserError(_("Please configure a sales journal for commission invoices."))
        return journal

    def _get_statement_clearing_journal(self):
        self.ensure_one()
        journal = self.company_id.mwanzo_commission_clearing_journal_id or self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", self.company_id.id)],
            limit=1,
        )
        if not journal:
            raise UserError(_("Please configure a general journal for commission clearing entries."))
        return journal

    def _get_vendor_payout_account(self):
        self.ensure_one()
        account = self.company_id.mwanzo_vendor_payout_account_id
        if not account:
            account = self.env["account.account"].search(
                [
                    ("code", "=", "511100"),
                    ("account_type", "=", "expense"),
                    ("company_id", "=", self.company_id.id),
                    ("deprecated", "=", False),
                ],
                limit=1,
            )
        if not account:
            account = self.env["account.account"].search(
                [
                    ("account_type", "=", "expense"),
                    ("company_id", "=", self.company_id.id),
                    ("deprecated", "=", False),
                ],
                limit=1,
            )
        if not account:
            raise UserError(_("Please configure a vendor payout expense account."))
        return account

    def _get_commission_income_account(self):
        self.ensure_one()
        account = self.company_id.mwanzo_commission_income_account_id
        if not account:
            account = self.env["account.account"].search(
                [
                    ("code", "=", "0000"),
                    ("account_type", "in", ("income", "income_other")),
                    ("company_id", "=", self.company_id.id),
                    ("deprecated", "=", False),
                ],
                limit=1,
            )
        if not account:
            account = self.env["account.account"].search(
                [
                    ("account_type", "in", ("income", "income_other")),
                    ("company_id", "=", self.company_id.id),
                    ("deprecated", "=", False),
                ],
                order="code asc",
                limit=1,
            )
        if not account:
            raise UserError(_("Please configure a commission income account."))
        return account

    def _create_vendor_payout_bill(self):
        self.ensure_one()
        move = self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.vendor_id.id,
            "invoice_date": fields.Date.context_today(self),
            "journal_id": self._get_statement_purchase_journal().id,
            "company_id": self.company_id.id,
            "invoice_origin": self.name,
            "ref": _("Vendor settlement %s") % self.name,
            "invoice_line_ids": [
                (
                    0,
                    0,
                    {
                        "name": _("Vendor gross sales collected for %s") % self.name,
                        "quantity": 1.0,
                        "price_unit": self.total_collected,
                        "account_id": self._get_vendor_payout_account().id,
                    },
                ),
            ],
        })
        move.action_post()
        return move

    def _create_commission_invoice(self):
        self.ensure_one()
        move = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.vendor_id.id,
            "invoice_date": fields.Date.context_today(self),
            "journal_id": self._get_statement_sale_journal().id,
            "company_id": self.company_id.id,
            "invoice_origin": self.name,
            "ref": _("Commission retained %s") % self.name,
            "invoice_line_ids": [
                (
                    0,
                    0,
                    {
                        "name": _("Commission retained for %s") % self.name,
                        "quantity": 1.0,
                        "price_unit": self.total_commission,
                        "account_id": self._get_commission_income_account().id,
                    },
                ),
            ],
        })
        move.action_post()
        return move

    def _create_commission_clearing_entry(self):
        self.ensure_one()
        bill = self.vendor_bill_id
        invoice = self.commission_invoice_id
        if not bill or not invoice:
            raise UserError(_("Create the vendor bill and commission invoice before clearing commission."))

        payable_account = self.vendor_id.property_account_payable_id
        receivable_account = self.vendor_id.property_account_receivable_id
        if not payable_account or not receivable_account:
            raise UserError(_("Please configure payable and receivable accounts on vendor %s.") % self.vendor_id.display_name)

        amount = min(self.total_commission, bill.amount_residual, invoice.amount_residual)
        if not amount:
            return False

        move = self.env["account.move"].create({
            "move_type": "entry",
            "date": fields.Date.context_today(self),
            "journal_id": self._get_statement_clearing_journal().id,
            "company_id": self.company_id.id,
            "ref": _("Commission clearing for %s") % self.name,
            "line_ids": [
                (
                    0,
                    0,
                    {
                        "name": _("Commission offset against vendor payable %s") % self.name,
                        "partner_id": self.vendor_id.id,
                        "account_id": payable_account.id,
                        "debit": amount,
                        "credit": 0.0,
                    },
                ),
                (
                    0,
                    0,
                    {
                        "name": _("Commission invoice paid by retention %s") % self.name,
                        "partner_id": self.vendor_id.id,
                        "account_id": receivable_account.id,
                        "debit": 0.0,
                        "credit": amount,
                    },
                ),
            ],
        })
        move.action_post()

        bill_lines = bill.line_ids.filtered(
            lambda line: line.account_id == payable_account and not line.reconciled
        )
        clearing_payable = move.line_ids.filtered(
            lambda line: line.account_id == payable_account and not line.reconciled
        )
        if bill_lines and clearing_payable:
            (bill_lines + clearing_payable).reconcile()

        invoice_lines = invoice.line_ids.filtered(
            lambda line: line.account_id == receivable_account and not line.reconciled
        )
        clearing_receivable = move.line_ids.filtered(
            lambda line: line.account_id == receivable_account and not line.reconciled
        )
        if invoice_lines and clearing_receivable:
            (invoice_lines + clearing_receivable).reconcile()

        invoice.invalidate_recordset(["amount_residual", "payment_state"])
        bill.invalidate_recordset(["amount_residual", "payment_state"])
        return move

    def action_export_csv(self):
        self.ensure_one()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for row in self._get_template_export_rows():
            writer.writerow(row)
        attachment = self.env["ir.attachment"].create(
            {
                "name": f"{self.name or 'vendor-statement'}.csv",
                "type": "binary",
                "datas": base64.b64encode(buffer.getvalue().encode("utf-8")),
                "mimetype": "text/csv",
                "res_model": self._name,
                "res_id": self.id,
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def action_export_xlsx(self):
        self.ensure_one()
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        worksheet = workbook.add_worksheet("Statement")
        title_format = workbook.add_format({"bold": True})
        header_format = workbook.add_format({"bold": True, "border": 1})
        text_format = workbook.add_format({})
        number_format = workbook.add_format({"num_format": "#,##0.00"})
        total_label_format = workbook.add_format({"bold": True, "top": 1})
        total_number_format = workbook.add_format({"bold": True, "top": 1, "num_format": "#,##0.00"})
        total_integer_format = workbook.add_format({"bold": True, "top": 1, "num_format": "#,##0"})

        rows = self._get_template_export_rows()
        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                if row_idx == 7:
                    worksheet.write(row_idx, col_idx, value, header_format)
                elif row_idx == len(rows) - 1:
                    if col_idx == 0:
                        worksheet.write(row_idx, col_idx, value, total_label_format)
                    elif isinstance(value, (int, float)):
                        fmt = total_integer_format if col_idx == 1 else total_number_format
                        worksheet.write_number(row_idx, col_idx, value, fmt)
                    else:
                        worksheet.write(row_idx, col_idx, value, total_label_format)
                elif row_idx < 5:
                    worksheet.write(row_idx, col_idx, value, title_format if col_idx == 0 else text_format)
                else:
                    if isinstance(value, (int, float)):
                        worksheet.write_number(row_idx, col_idx, value, number_format)
                    else:
                        worksheet.write(row_idx, col_idx, value, text_format)

        worksheet.set_column("A:A", 24)
        worksheet.set_column("B:K", 14)
        workbook.close()
        output.seek(0)

        attachment = self.env["ir.attachment"].create(
            {
                "name": f"{self.name or 'vendor-statement'}.xlsx",
                "type": "binary",
                "datas": base64.b64encode(output.read()),
                "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "res_model": self._name,
                "res_id": self.id,
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def action_open_export_wizard(self):
        self.ensure_one()
        return {
            "name": _("Export Vendor Statement"),
            "type": "ir.actions.act_window",
            "res_model": "mwanzo.vendor.statement.export.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_statement_id": self.id,
            },
        }

    def _get_template_export_rows(self):
        self.ensure_one()
        theme_names = sorted({line.theme_id.name for line in self.line_ids if line.theme_id.name})
        rows = [
            ["Vendor", self.vendor_id.display_name or ""],
            ["Date From", fields.Date.to_string(self.date_from) if self.date_from else ""],
            ["Date To", fields.Date.to_string(self.date_to) if self.date_to else ""],
            ["Theme", ", ".join(theme_names)],
            ["Statement No", self.name or ""],
            [],
            [],
            ["Product", "Quantity", "Qty Left", "Collected", "Commission %", "Commission", "VAT %", "VAT", "Sales Excl.", "Discount", "Payable"],
        ]
        total_quantity = 0.0
        total_qty_left = 0.0
        total_collected = 0.0
        total_commission_pct = 0.0
        total_commission = 0.0
        total_vat_pct = 0.0
        total_vat = 0.0
        total_sales = 0.0
        total_discount = 0.0
        total_payable = 0.0
        for line in self.line_ids:
            rows.append([
                line.product_id.display_name or "",
                line.quantity or 0.0,
                line.quantity_remaining or 0.0,
                line.collected_amount or 0.0,
                line.commission_percentage or 0.0,
                line.commission_amount or 0.0,
                line.vat_rate or 0.0,
                line.vat_amount or 0.0,
                line.sale_amount or 0.0,
                line.discount_amount or 0.0,
                line.net_amount or 0.0,
            ])
            total_quantity += line.quantity or 0.0
            total_qty_left += line.quantity_remaining or 0.0
            total_collected += line.collected_amount or 0.0
            total_commission_pct += line.commission_percentage or 0.0
            total_commission += line.commission_amount or 0.0
            total_vat_pct += line.vat_rate or 0.0
            total_vat += line.vat_amount or 0.0
            total_sales += line.sale_amount or 0.0
            total_discount += line.discount_amount or 0.0
            total_payable += line.net_amount or 0.0
        rows.append([])
        rows.append([
            "Total",
            total_quantity,
            total_qty_left,
            total_collected,
            total_commission_pct,
            total_commission,
            total_vat_pct,
            total_vat,
            total_sales,
            total_discount,
            total_payable,
        ])
        return rows


class MwanzoVendorStatementLine(models.Model):
    _name = "mwanzo.vendor.statement.line"
    _description = "Mwanzo Vendor Statement Line"

    statement_id = fields.Many2one(
        "mwanzo.vendor.statement",
        required=True,
        ondelete="cascade",
    )
    statement_name = fields.Char(related="statement_id.name", store=True, readonly=True)
    vendor_id = fields.Many2one("res.partner", related="statement_id.vendor_id", store=True, readonly=True)
    date_from = fields.Date(related="statement_id.date_from", store=True, readonly=True)
    date_to = fields.Date(related="statement_id.date_to", store=True, readonly=True)
    pos_order_line_id = fields.Many2one("pos.order.line", required=True)
    pos_order_line_ids = fields.One2many(
        "pos.order.line",
        "mwanzo_vendor_statement_line_id",
        string="POS Lines",
        readonly=True,
    )
    product_id = fields.Many2one(
        "product.product",
        store=True,
        readonly=True,
    )
    theme_id = fields.Many2one(
        "mwanzo.market.theme",
        store=True,
        readonly=True,
    )
    commission_percentage = fields.Float(string="Commission %")
    quantity = fields.Float(string="Quantity", readonly=True)
    quantity_remaining = fields.Float(string="Qty Left", compute="_compute_quantity_remaining", readonly=True)
    collected_amount = fields.Monetary(
        string="Collected",
        store=True,
        readonly=True,
    )
    discount_amount = fields.Monetary(
        string="Discount",
        store=True,
        readonly=True,
    )
    vat_rate = fields.Float(string="VAT %", store=True, readonly=True)
    vat_amount = fields.Monetary(string="VAT", store=True, readonly=True)
    sale_amount = fields.Monetary(string="Sales Excl.")
    commission_amount = fields.Monetary(string="Commission", store=True, readonly=True)
    net_amount = fields.Monetary(string="Payable", store=True, readonly=True)
    currency_id = fields.Many2one(
        "res.currency",
        related="statement_id.currency_id",
        store=True,
        readonly=True,
    )

    @api.depends("product_id")
    def _compute_quantity_remaining(self):
        for line in self:
            line.quantity_remaining = line.product_id.qty_available or 0.0

    def unlink(self):
        self.mapped("pos_order_line_ids").write({"mwanzo_vendor_statement_line_id": False})
        return super().unlink()


class MwanzoVendorStatementExportWizard(models.TransientModel):
    _name = "mwanzo.vendor.statement.export.wizard"
    _description = "Vendor Statement Export Wizard"

    statement_id = fields.Many2one("mwanzo.vendor.statement", required=True, readonly=True)
    export_format = fields.Selection(
        [("csv", "CSV"), ("xlsx", "XLSX")],
        required=True,
        default="xlsx",
    )

    def action_export(self):
        self.ensure_one()
        if self.export_format == "csv":
            return self.statement_id.action_export_csv()
        return self.statement_id.action_export_xlsx()


class MwanzoVendorSettlementWizard(models.TransientModel):
    _name = "mwanzo.vendor.settlement.wizard"
    _description = "Mwanzo Vendor Settlement Wizard"

    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    vendor_id = fields.Many2one("res.partner", domain=[("is_mwanzo_vendor", "=", True)])
    theme_ids = fields.Many2many("mwanzo.market.theme", string="Themes")
    settlement_run_id = fields.Many2one("mwanzo.settlement.run", string="Settlement Run")

    def _get_existing_matching_statements(self):
        self.ensure_one()
        domain = [
            ("date_from", "<=", self.date_to),
            ("date_to", ">=", self.date_from),
        ]
        if self.vendor_id:
            domain.append(("vendor_id", "=", self.vendor_id.id))
        statements = self.env["mwanzo.vendor.statement"].search(domain, order="name")
        if self.theme_ids:
            statements = statements.filtered(
                lambda statement: bool(statement.line_ids.filtered(lambda line: line.theme_id in self.theme_ids))
            )
        return statements

    def _action_open_statements(self, statements, name=None):
        return {
            "type": "ir.actions.act_window",
            "name": name or _("Vendor Statements"),
            "res_model": "mwanzo.vendor.statement",
            "view_mode": "tree,form",
            "domain": [("id", "in", statements.ids)],
        }

    def action_generate_statements(self):
        self.ensure_one()
        pos_lines = self.env["mwanzo.vendor.statement"]._get_settlement_pos_lines(
            self.date_from,
            self.date_to,
            vendor=self.vendor_id,
            themes=self.theme_ids,
        )
        if not pos_lines:
            existing_statements = self._get_existing_matching_statements()
            if existing_statements:
                return self._action_open_statements(
                    existing_statements,
                    _("Existing Vendor Statements"),
                )
            raise UserError(
                _(
                    "No unsettled POS order lines found for the given filters. "
                    "Matching sales may already be linked to vendor statements."
                )
            )

        vendors = pos_lines.mapped("mwanzo_vendor_id")
        statements = self.env["mwanzo.vendor.statement"]

        for vendor in vendors:
            vendor_lines = pos_lines.filtered(lambda l: l.mwanzo_vendor_id == vendor)
            statement_domain = [
                ("vendor_id", "=", vendor.id),
                ("date_from", "=", self.date_from),
                ("date_to", "=", self.date_to),
                ("state", "in", ("draft", "confirmed")),
            ]
            if self.settlement_run_id:
                statement_domain.append(("settlement_run_id", "=", self.settlement_run_id.id))
            statement = statements.search(statement_domain, limit=1)
            if statement and statement.state == "confirmed":
                statement.state = "draft"
                statement._update_stage_from_state()
            if not statement:
                statement_vals = {
                    "vendor_id": vendor.id,
                    "date_from": self.date_from,
                    "date_to": self.date_to,
                    "company_id": self.env.company.id,
                }
                if self.settlement_run_id:
                    statement_vals["settlement_run_id"] = self.settlement_run_id.id
                statement = statements.create(statement_vals)

            existing_pos_lines = statement.line_ids.mapped("pos_order_line_ids") | statement.line_ids.mapped("pos_order_line_id")
            existing_pos_lines = existing_pos_lines.filtered(lambda line: line)
            combined_lines = existing_pos_lines | vendor_lines
            existing_pos_lines.write({"mwanzo_vendor_statement_line_id": False})
            statement.line_ids.unlink()
            statement._create_grouped_statement_lines(combined_lines)
            statements |= statement

        return self._action_open_statements(statements)
