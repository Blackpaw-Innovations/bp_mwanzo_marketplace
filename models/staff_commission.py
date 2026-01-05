from datetime import datetime, time, timedelta

from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    mwanzo_daily_target_amount = fields.Monetary(
        string="Mwanzo Daily Target",
        help="Daily sales target for commission.",
    )
    mwanzo_commission_rate = fields.Float(
        string="Mwanzo Commission Rate",
        help="Percentage commission on net sales when target met.",
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )


class PosOrder(models.Model):
    _inherit = "pos.order"

    mwanzo_sales_employee_id = fields.Many2one("hr.employee", string="Sales Associate")
    mwanzo_cashier_employee_id = fields.Many2one("hr.employee", string="Cashier")


class MwanzoStaffCommissionPolicy(models.Model):
    _name = "mwanzo.staff.commission.policy"
    _description = "Mwanzo Staff Commission Policy"

    name = fields.Char(required=True)
    role = fields.Selection(
        [("sales_associate", "Sales Associate"), ("cashier", "Cashier")],
        required=True,
    )
    default_daily_target = fields.Monetary()
    default_commission_rate = fields.Float()
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )


class MwanzoStaffCommissionLedger(models.Model):
    _name = "mwanzo.staff.commission.ledger"
    _description = "Mwanzo Staff Commission Ledger"

    employee_id = fields.Many2one("hr.employee", required=True)
    date = fields.Date(required=True)
    total_sales = fields.Monetary()
    target_amount = fields.Monetary()
    commission_rate = fields.Float()
    commission_amount = fields.Monetary()
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed"), ("paid", "Paid")],
        default="draft",
        required=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="employee_id.company_id.currency_id",
        store=True,
        readonly=True,
    )

    @api.model
    def _cron_compute_staff_commissions(self):
        target_date = fields.Date.context_today(self) - timedelta(days=1)
        start_dt = datetime.combine(target_date, time.min)
        end_dt = datetime.combine(target_date + timedelta(days=1), time.min)

        def _aggregate_by_field(field_name):
            domain = [
                ("date_order", ">=", fields.Datetime.to_string(start_dt)),
                ("date_order", "<", fields.Datetime.to_string(end_dt)),
                (field_name, "!=", False),
            ]
            data = (
                self.env["pos.order"]
                .read_group(domain, ["amount_total", field_name], [field_name])
            )
            result = {}
            for entry in data:
                field_val = entry.get(field_name)
                emp_id = field_val[0] if field_val else False
                if emp_id:
                    result[emp_id] = result.get(emp_id, 0.0) + entry.get(
                        "amount_total", 0.0
                    )
            return result

        sales_totals = _aggregate_by_field("mwanzo_sales_employee_id")
        cashier_totals = _aggregate_by_field("mwanzo_cashier_employee_id")

        totals_by_emp = sales_totals.copy()
        for emp_id, amount in cashier_totals.items():
            totals_by_emp[emp_id] = totals_by_emp.get(emp_id, 0.0) + amount

        employees = self.env["hr.employee"].browse(totals_by_emp.keys())
        policies = {
            "sales_associate": self._get_policy("sales_associate"),
            "cashier": self._get_policy("cashier"),
        }

        for employee in employees:
            total_sales = totals_by_emp.get(employee.id, 0.0)
            role = "sales_associate" if employee.id in sales_totals else "cashier"
            policy = policies.get(role)
            target = (
                employee.mwanzo_daily_target_amount
                or (policy.default_daily_target if policy else 0.0)
            )
            rate = (
                employee.mwanzo_commission_rate
                or (policy.default_commission_rate if policy else 0.0)
            )
            commission_amount = total_sales * rate / 100.0 if total_sales >= target else 0.0

            ledger = self.search(
                [("employee_id", "=", employee.id), ("date", "=", target_date)],
                limit=1,
            )
            vals = {
                "employee_id": employee.id,
                "date": target_date,
                "total_sales": total_sales,
                "target_amount": target,
                "commission_rate": rate,
                "commission_amount": commission_amount,
            }
            if ledger:
                ledger.write(vals)
            else:
                self.create(vals)

    def _get_policy(self, role):
        return (
            self.env["mwanzo.staff.commission.policy"]
            .search([("role", "=", role)], limit=1)
        )
