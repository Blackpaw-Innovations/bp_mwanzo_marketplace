import json
from datetime import date, timedelta

from odoo import fields, http
from odoo.http import request


class MarketHubDashboard(http.Controller):
    @http.route("/market_hub/dashboard", type="http", auth="user")
    def market_hub_dashboard(self, **kwargs):
        env = request.env
        range_key = kwargs.get("range", "7d")
        custom_start = kwargs.get("date_from")
        custom_end = kwargs.get("date_to")

        today = fields.Date.context_today(env.user)
        start_date = today - timedelta(days=6)
        end_date = today

        if range_key == "30d":
            start_date = today - timedelta(days=29)
        elif range_key == "90d":
            start_date = today - timedelta(days=89)
        elif range_key == "365d":
            start_date = today - timedelta(days=364)
        elif range_key == "ytd":
            start_date = date(today.year, 1, 1)
        elif range_key == "custom":
            try:
                start_date = fields.Date.from_string(custom_start) or start_date
                end_date = fields.Date.from_string(custom_end) or end_date
            except Exception:
                pass

        range_label = {
            "7d": "Last 7 days",
            "30d": "Last 30 days",
            "90d": "Last 90 days",
            "365d": "Last 12 months",
            "ytd": "Year to date",
            "custom": "Custom range",
        }.get(range_key, "Last 7 days")

        # POS sales (last 7 days)
        pos_domain = [
            ("date_order", ">=", start_date),
            ("date_order", "<=", end_date),
            ("state", "!=", "cancel"),
        ]
        pos_orders = env["pos.order"].sudo().search(pos_domain)
        pos_total = sum(pos_orders.mapped("amount_total"))
        pos_count = len(pos_orders)
        avg_ticket = pos_total / pos_count if pos_count else 0.0

        # Net payable to vendors (draft/confirmed/invoiced statements)
        statements = env["mwanzo.vendor.statement"].sudo().search([
            ("state", "in", ["draft", "confirmed", "invoiced"])
        ])
        net_payable = sum(statements.mapped("total_net_payable"))
        draft_statements = len(statements.filtered(lambda s: s.state == "draft"))

        # Licenses expiring soon (next 30 days)
        expiring_domain = [
            ("state", "=", "active"),
            ("date_end", ">=", today),
            ("date_end", "<=", today + timedelta(days=30)),
        ]
        expiring_count = env["mwanzo.vendor.license"].sudo().search_count(expiring_domain)
        sample_expiring = env["mwanzo.vendor.license"].sudo().search(
            expiring_domain, order="date_end asc", limit=3
        )
        
        # License counts by state
        license_counts = {
            "active": env["mwanzo.vendor.license"].sudo().search_count([("state", "=", "active")]),
            "draft": env["mwanzo.vendor.license"].sudo().search_count([("state", "=", "draft")]),
            "expired": env["mwanzo.vendor.license"].sudo().search_count([("state", "=", "expired")]),
            "expiring": expiring_count,
        }

        # Stock intake sessions (last 7 days)
        intake_domain = [
            ("date", ">=", start_date),
            ("date", "<=", end_date),
        ]
        intake_sessions = env["mwanzo.stock.intake.session"].sudo().search(intake_domain)
        intake_total = len(intake_sessions)
        intake_pending = len(intake_sessions.filtered(lambda s: s.state != "stocked"))
        recent_intakes = intake_sessions.sorted(lambda s: s.date, reverse=True)[:5]

        # Theme share by POS sales
        line_domain = [
            ("order_id.date_order", ">=", start_date),
            ("order_id.date_order", "<=", today),
            ("order_id.state", "!=", "cancel"),
            ("mwanzo_theme_id", "!=", False),
        ]
        theme_data = env["pos.order.line"].sudo().read_group(
            line_domain,
            ["price_subtotal_incl", "mwanzo_theme_id"],
            ["mwanzo_theme_id"],
        )
        
        total_sales_all_themes = sum(d.get("price_subtotal_incl", 0.0) for d in theme_data)
        
        theme_totals = []
        for entry in theme_data:
            theme_id = entry["mwanzo_theme_id"][0] if entry.get("mwanzo_theme_id") else False
            theme = env["mwanzo.market.theme"].browse(theme_id) if theme_id else False
            
            if not theme:
                continue
                
            total = entry.get("price_subtotal_incl", 0.0)
            percentage = (total / total_sales_all_themes * 100) if total_sales_all_themes else 0.0
            
            # Spaces count
            spaces_count = env["mwanzo.market.space"].sudo().search_count([("theme_id", "=", theme.id)])
            
            # Vendors count (unique vendors with active licenses for this theme)
            # Or simpler: count unique vendors in the sales data for this period
            # Let's use unique vendors in sales data for "active" vendors
            vendor_data = env["pos.order.line"].sudo().read_group(
                line_domain + [("mwanzo_theme_id", "=", theme.id)],
                ["mwanzo_vendor_id"],
                ["mwanzo_vendor_id"],
            )
            vendors_count = len(vendor_data)
            
            # Top Vendor
            top_vendor_data = env["pos.order.line"].sudo().read_group(
                line_domain + [("mwanzo_theme_id", "=", theme.id)],
                ["price_subtotal_incl", "mwanzo_vendor_id"],
                ["mwanzo_vendor_id"],
                orderby="price_subtotal_incl desc",
                limit=1
            )
            top_vendor_name = "None"
            if top_vendor_data:
                vendor_id = top_vendor_data[0]["mwanzo_vendor_id"][0] if top_vendor_data[0].get("mwanzo_vendor_id") else False
                if vendor_id:
                    top_vendor_name = env["res.partner"].browse(vendor_id).name

            theme_totals.append({
                "name": theme.name,
                "total": total,
                "percentage": round(percentage),
                "spaces": spaces_count,
                "vendors": vendors_count,
                "top_vendor": top_vendor_name,
            })
            
        theme_totals = sorted(theme_totals, key=lambda t: t["total"], reverse=True)[:4]

        # Daily Sales Chart Data
        chart_domain = [
            ("order_id.date_order", ">=", start_date),
            ("order_id.date_order", "<=", end_date),
            ("order_id.state", "!=", "cancel"),
        ]
        # Cannot group by order_id:day directly on lines, so group by order first
        line_data = env["pos.order.line"].sudo().read_group(
            chart_domain,
            ["price_subtotal_incl", "order_id", "mwanzo_theme_id"],
            ["order_id", "mwanzo_theme_id"],
            lazy=False
        )

        # Collect order IDs to fetch dates
        order_ids = set()
        for d in line_data:
            if d.get("order_id"):
                order_ids.add(d["order_id"][0])
        
        # Fetch order dates and map to day string
        orders = env["pos.order"].sudo().browse(list(order_ids)).read(["date_order"])
        order_date_map = {
            o["id"]: o["date_order"].strftime("%Y-%m-%d") if o["date_order"] else False
            for o in orders
        }
        
        # Aggregate by day and theme
        chart_dates = set()
        chart_themes = set()
        daily_agg = {} # (day, theme_name) -> amount

        for d in line_data:
            if not d.get("order_id") or not d.get("mwanzo_theme_id"):
                continue
                
            order_id = d["order_id"][0]
            day = order_date_map.get(order_id)
            if not day:
                continue
                
            theme_name = d["mwanzo_theme_id"][1]
            amount = d.get("price_subtotal_incl", 0.0)
            
            chart_dates.add(day)
            chart_themes.add(theme_name)
            
            key = (day, theme_name)
            daily_agg[key] = daily_agg.get(key, 0.0) + amount

        chart_dates = sorted(list(chart_dates))
        chart_themes = sorted(list(chart_themes))
        
        sales_chart_data = {
            "labels": chart_dates,
            "datasets": []
        }
        
        colors = [
            'rgba(59, 130, 246, 0.7)',  # Blue
            'rgba(139, 92, 246, 0.7)', # Purple
            'rgba(16, 185, 129, 0.7)', # Green
            'rgba(245, 158, 11, 0.7)', # Orange
            'rgba(239, 68, 68, 0.7)',  # Red
        ]
        
        for i, theme_name in enumerate(chart_themes):
            data = []
            for day in chart_dates:
                amount = daily_agg.get((day, theme_name), 0.0)
                data.append(amount)
            
            sales_chart_data["datasets"].append({
                "label": theme_name,
                "data": data,
                "backgroundColor": colors[i % len(colors)],
                "borderRadius": 6
            })

        # Vendor settlements status counts
        settlement_counts = {
            "pending": env["mwanzo.vendor.statement"].sudo().search_count([("state", "=", "draft")]),
            "draft": env["mwanzo.vendor.statement"].sudo().search_count([("state", "=", "draft")]),
            "to_invoice": env["mwanzo.vendor.statement"].sudo().search_count([("state", "=", "confirmed")]),
        }
        latest_statements = env["mwanzo.vendor.statement"].sudo().search([], order="create_date desc", limit=5)

        # Staff commissions (if ledger model exists)
        commission_total = 0.0
        commission_staff = 0
        commission_chart = []
        top_staff = False
        Ledger = env.get("mwanzo.staff.commission.ledger")
        if Ledger:
            yesterday = today - timedelta(days=1)
            ledger_qs = Ledger.sudo().search([("date", "=", yesterday)])
            commission_total = sum(ledger_qs.mapped("commission_amount"))
            commission_staff = len(ledger_qs)
            commission_chart = [
                {"label": l.employee_id.name or "Employee", "value": l.commission_amount}
                for l in ledger_qs.sorted(lambda r: r.commission_amount, reverse=True)[:5]
            ]
            top_staff = ledger_qs.sorted(lambda r: r.commission_amount, reverse=True)[:1]
            top_staff = top_staff[0] if top_staff else False

        values = {
            "today": today,
            "start_date": start_date,
            "end_date": end_date,
            "range_key": range_key,
            "range_label": range_label,
            "pos_total": pos_total,
            "avg_ticket": avg_ticket,
            "net_payable": net_payable,
            "draft_statements": draft_statements,
            "expiring_count": expiring_count,
            "sample_expiring": sample_expiring,
            "intake_total": intake_total,
            "intake_pending": intake_pending,
            "recent_intakes": recent_intakes,
            "theme_totals": theme_totals,
            "settlement_counts": settlement_counts,
            "latest_statements": latest_statements,
            "commission_total": commission_total,
            "commission_staff": commission_staff,
            "commission_chart_json": json.dumps(commission_chart),
            "pos_chart_json": json.dumps(
                [{"label": t["name"], "value": t["total"]} for t in theme_totals]
            ),
            "sales_chart_json": json.dumps(sales_chart_data),
            "license_counts": license_counts,
            "top_staff": top_staff,
            "company": env.company,
        }
        return request.render("bp_mwanzo_marketplace.market_hub_dashboard", values)
