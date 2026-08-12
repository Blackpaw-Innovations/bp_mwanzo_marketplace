import base64
import csv
import io
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
    commission_percentage = fields.Float(string="Commission %")
    quantity = fields.Float(string="Quantity", related="pos_order_line_id.qty", store=True, readonly=True)
    quantity_remaining = fields.Float(string="Qty Left", compute="_compute_quantity_remaining", readonly=True)
    collected_amount = fields.Monetary(
        string="Collected",
        compute="_compute_pricing_amounts",
        store=True,
        readonly=True,
    )
    discount_amount = fields.Monetary(
        string="Discount",
        compute="_compute_pricing_amounts",
        store=True,
        readonly=True,
    )
    vat_rate = fields.Float(string="VAT %", compute="_compute_vat_rate", inverse="_inverse_vat_rate", store=True)
    vat_amount = fields.Monetary(string="VAT", compute="_compute_pricing_amounts", store=True, readonly=True)
    sale_amount = fields.Monetary(string="Sales Excl.")
    commission_amount = fields.Monetary(string="Commission", compute="_compute_pricing_amounts", store=True, readonly=True)
    net_amount = fields.Monetary(string="Payable", compute="_compute_pricing_amounts", store=True, readonly=True)
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

    @api.depends("pos_order_line_id.price_subtotal", "pos_order_line_id.price_subtotal_incl")
    def _compute_vat_rate(self):
        for line in self:
            subtotal = line.pos_order_line_id.price_subtotal or 0.0
            tax_amount = (line.pos_order_line_id.price_subtotal_incl or 0.0) - subtotal
            line.vat_rate = (tax_amount / subtotal * 100.0) if subtotal else 0.0

    def _inverse_vat_rate(self):
        # Keep the manually entered value; the amount fields react through onchange/compute.
        return

    @api.depends(
        "sale_amount",
        "commission_percentage",
        "vat_rate",
        "pos_order_line_id.price_subtotal_incl",
        "pos_order_line_id.price_unit",
        "pos_order_line_id.qty",
    )
    def _compute_pricing_amounts(self):
        for line in self:
            sale_amount = line.sale_amount or 0.0
            commission_percentage = line.commission_percentage or 0.0
            vat_rate = line.vat_rate or 0.0
            list_amount = (line.pos_order_line_id.price_unit or 0.0) * (line.pos_order_line_id.qty or 0.0)
            line.collected_amount = line.pos_order_line_id.price_subtotal_incl or 0.0
            line.discount_amount = max(list_amount - sale_amount, 0.0)
            line.vat_amount = sale_amount * vat_rate / 100.0
            gross_amount = sale_amount + line.vat_amount
            line.commission_amount = gross_amount * commission_percentage / 100.0
            line.net_amount = gross_amount - line.commission_amount

    @api.onchange("sale_amount", "commission_percentage", "vat_rate")
    def _onchange_pricing_amounts(self):
        self._compute_pricing_amounts()


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
                vat_amount = (line.price_subtotal_incl or 0.0) - sale_amount
                commission_percentage = line.mwanzo_commission_percentage or 0.0
                gross_amount = sale_amount + vat_amount
                commission_amount = gross_amount * commission_percentage / 100.0
                net_amount = gross_amount - commission_amount
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
