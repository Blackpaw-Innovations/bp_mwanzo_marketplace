from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MwanzoStockIntakeStage(models.Model):
    _name = "mwanzo.stock.intake.stage"
    _description = "Mwanzo Stock Intake Stage"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=1)
    fold = fields.Boolean(string="Folded in Kanban")
    active = fields.Boolean(default=True)
    target_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("sent", "Request Sent"),
            ("vendor_confirm", "Confirmed"),
            ("stocked", "Received"),
        ],
        string="Related State",
        help="If set, moving a session to this stage will automatically set its state.",
    )


class MwanzoStockIntakeSession(models.Model):
    _name = "mwanzo.stock.intake.session"
    _description = "Mwanzo Stock Intake Session"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        readonly=True,
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code(
            "mwanzo.stock.intake.session"
        ),
    )
    vendor_id = fields.Many2one(
        "res.partner",
        required=True,
    )
    theme_id = fields.Many2one("mwanzo.market.theme")
    date = fields.Date(default=fields.Date.context_today)
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )
    line_ids = fields.One2many(
        "mwanzo.stock.intake.line",
        "session_id",
        string="Lines",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("sent", "Request Sent"),
            ("vendor_confirm", "Confirmed"),
            ("stocked", "Received"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    stage_id = fields.Many2one(
        "mwanzo.stock.intake.stage",
        string="Stage",
        group_expand="_read_group_stage_ids",
        copy=False,
        index=True,
        default=lambda self: self.env.ref("bp_mwanzo_marketplace.stage_intake_draft", raise_if_not_found=False),
        tracking=True,
    )
    picking_count = fields.Integer(compute="_compute_picking_count")

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        return self.env['mwanzo.stock.intake.stage'].search([], order=order)

    def write(self, vals):
        if 'stage_id' in vals:
            stage = self.env['mwanzo.stock.intake.stage'].browse(vals['stage_id'])
            if stage.target_state:
                vals['state'] = stage.target_state
        return super().write(vals)

    def _update_stage_from_state(self):
        for record in self:
            if not record.state:
                continue
            stage = self.env['mwanzo.stock.intake.stage'].search([('target_state', '=', record.state)], limit=1)
            if stage and stage != record.stage_id:
                record.stage_id = stage

    def _compute_picking_count(self):
        for session in self:
            session.picking_count = self.env["stock.picking"].search_count(
                [("origin", "=", session.name)]
            )

    def action_view_pickings(self):
        self.ensure_one()
        pickings = self.env["stock.picking"].search([("origin", "=", self.name)])
        action = {
            "name": _("Intake Pickings"),
            "type": "ir.actions.act_window",
            "res_model": "stock.picking",
            "domain": [("id", "in", pickings.ids)],
            "context": {"default_origin": self.name},
        }
        if len(pickings) == 1:
            action.update(
                {
                    "view_mode": "form",
                    "res_id": pickings.id,
                }
            )
        else:
            action["view_mode"] = "tree,form"
        return action

    def action_send_email(self):
        self.ensure_one()
        template_id = self.env.ref("bp_mwanzo_marketplace.email_template_mwanzo_stock_intake").id
        lang = self.env.context.get("lang")
        if self.vendor_id.lang:
            lang = self.vendor_id.lang
        
        ctx = {
            "default_model": "mwanzo.stock.intake.session",
            "default_res_ids": self.ids,
            "default_template_id": template_id,
            "default_composition_mode": "comment",
            "mark_so_as_sent": True,
            "custom_layout": "mail.mail_notification_light",
            "force_email": True,
            "model_description": _("Stock Intake Request"),
        }
        return {
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "mail.compose.message",
            "views": [(False, "form")],
            "view_id": False,
            "target": "new",
            "context": ctx,
        }

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        if self.env.context.get('mark_so_as_sent'):
            self.filtered(lambda o: o.state == 'draft').write({'state': 'sent'})
            self._update_stage_from_state()
        return super(MwanzoStockIntakeSession, self.with_context(mail_post_autofollow=True)).message_post(**kwargs)

    @api.constrains("vendor_id", "theme_id", "date")
    def _check_vendor_has_active_license(self):
        for session in self:
            if not session.vendor_id:
                continue
            date_check = session.date or fields.Date.context_today(session)
            license_domain = [
                ("vendor_id", "=", session.vendor_id.id),
                ("state", "=", "active"),
                ("date_start", "<=", date_check),
                ("date_end", ">=", date_check),
            ]
            if session.theme_id:
                license_domain.append('|')
                license_domain.append(("theme_ids", "in", session.theme_id.id))
                license_domain.append(("theme_ids", "=", False))
            has_license = bool(self.env["mwanzo.vendor.license"].search_count(license_domain))
            if not has_license:
                raise ValidationError(
                    _(
                        "Selected vendor does not have an active license for the chosen date%s."
                    )
                    % (" and theme" if session.theme_id else "")
                )

    def action_vendor_confirm(self):
        for session in self:
            if not session.line_ids:
                raise UserError(_("Add at least one line before confirming."))
            session.state = "vendor_confirm"
            session._update_stage_from_state()

    def action_receive_items(self):
        for session in self:
            if session.state == "stocked":
                raise UserError(_("This intake session is already processed."))
            if not session.line_ids:
                raise UserError(_("Add at least one line before validating."))

            # Auto-fill received_qty if 0
            for line in session.line_ids:
                if line.received_qty == 0.0 and line.expected_qty > 0.0:
                    line.received_qty = line.expected_qty
                
                # Auto-fill Mwanzo fields on the product
                if session.vendor_id:
                    line.product_id.mwanzo_vendor_id = session.vendor_id
                if session.theme_id:
                    line.product_id.mwanzo_theme_id = session.theme_id

            picking_type = session._get_incoming_picking_type()
            dest_location = session.company_id.mwanzo_store_location_id or picking_type.default_location_dest_id
            if not dest_location:
                raise UserError(_("Configure a destination location for intake."))
            source_location = (
                session.vendor_id.property_stock_supplier
                or picking_type.default_location_src_id
            )
            if not source_location:
                raise UserError(_("No source location found for the vendor intake."))

            move_vals_list = []
            for line in session.line_ids:
                if line.received_qty <= 0:
                    continue
                move_vals_list.append(
                    (
                        0,
                        0,
                        {
                            "name": line.product_id.display_name,
                            "product_id": line.product_id.id,
                            "product_uom_qty": line.received_qty,
                            "product_uom": line.uom_id.id,
                            "location_id": source_location.id,
                            "location_dest_id": dest_location.id,
                            "company_id": session.company_id.id,
                        },
                    )
                )

            if not move_vals_list:
                raise UserError(_("All lines have zero quantity; nothing to receive."))

            picking = self.env["stock.picking"].create(
                {
                    "picking_type_id": picking_type.id,
                    "location_id": source_location.id,
                    "location_dest_id": dest_location.id,
                    "owner_id": session.vendor_id.id,
                    "company_id": session.company_id.id,
                    "origin": session.name,
                    "move_ids_without_package": move_vals_list,
                }
            )
            picking.action_confirm()
            picking.action_assign()
            for move in picking.move_ids:
                move.picked = True
                for line in move.move_line_ids:
                    line.quantity = line.quantity_product_uom
            
            picking.button_validate()
            session.state = "stocked"
            session._update_stage_from_state()
        return True

    def _get_incoming_picking_type(self):
        picking_type = self.env["stock.picking.type"].search(
            [
                ("code", "=", "incoming"),
                ("warehouse_id.company_id", "=", self.company_id.id),
            ],
            limit=1,
        )
        if not picking_type:
            picking_type = self.env["stock.picking.type"].search(
                [("code", "=", "incoming"), ("warehouse_id", "=", False)],
                limit=1,
            )
        if not picking_type:
            raise UserError(_("No incoming picking type found for this company."))
        return picking_type

    @api.model
    def _cron_auto_create_intake_sessions(self):
        """
        Cron job to check product stock levels against mwanzo_min_qty.
        If stock < min_qty, add product to a draft intake session for that vendor.
        """
        # Find products below minimum quantity
        # We need to check qty_available. Since this is a stored field on product.product, we can search directly.
        # However, qty_available depends on location context. By default, it's all internal locations.
        # We assume standard stock checking.
        
        products_to_replenish = self.env['product.product'].search([
            ('mwanzo_min_qty', '>', 0),
            ('mwanzo_vendor_id', '!=', False),
            ('type', '=', 'product'), # Only storable products
        ])

        # Filter in python to ensure we check the correct quantity (e.g. available quantity)
        # Or we can trust the search if we add a domain for qty_available < mwanzo_min_qty?
        # Odoo domains don't support field comparison (qty_available < mwanzo_min_qty) directly in simple search.
        # So we iterate.
        
        vendor_products = {}

        for product in products_to_replenish:
            if product.qty_available < product.mwanzo_min_qty:
                vendor = product.mwanzo_vendor_id
                if vendor not in vendor_products:
                    vendor_products[vendor] = []
                vendor_products[vendor].append(product)

        for vendor, products in vendor_products.items():
            # Check for existing draft session
            session = self.search([
                ('vendor_id', '=', vendor.id),
                ('state', '=', 'draft'),
                ('company_id', '=', self.env.company.id)
            ], limit=1)

            if not session:
                session = self.create({
                    'vendor_id': vendor.id,
                    'company_id': self.env.company.id,
                    'date': fields.Date.context_today(self),
                })

            # Add lines
            for product in products:
                # Check if product already in session
                existing_line = session.line_ids.filtered(lambda l: l.product_id == product)
                
                qty_to_add = product.mwanzo_replenish_qty
                if qty_to_add <= 0:
                    # Default to bringing it up to min_qty + 1 if replenish not set? 
                    # Or just skip? User said "set quantity to like 20".
                    # Let's default to a sensible fallback if 0, maybe 1? Or just skip.
                    # If 0, we assume they haven't configured it properly, but let's default to (Min - Current) + 1
                    qty_to_add = (product.mwanzo_min_qty - product.qty_available) + 1
                    if qty_to_add < 1: 
                        qty_to_add = 1

                if existing_line:
                    # If already there, do we update? 
                    # Maybe ensure the expected qty is at least the replenish qty?
                    # Let's just leave it if it's already there to avoid overwriting manual changes.
                    pass
                else:
                    self.env['mwanzo.stock.intake.line'].create({
                        'session_id': session.id,
                        'product_id': product.id,
                        'expected_qty': qty_to_add,
                        'received_qty': 0.0, # Default to 0 until received
                        'uom_id': product.uom_id.id,
                    })



class MwanzoStockIntakeLine(models.Model):
    _name = "mwanzo.stock.intake.line"
    _description = "Mwanzo Stock Intake Line"

    session_id = fields.Many2one(
        "mwanzo.stock.intake.session",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        "product.product",
        required=True,
    )
    expected_qty = fields.Float()
    received_qty = fields.Float(required=True)
    uom_id = fields.Many2one(
        "uom.uom",
        default=lambda self: self.env.ref("uom.product_uom_unit"),
        required=True,
    )

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self:
            if line.product_id:
                line.uom_id = line.product_id.uom_id

    @api.constrains("received_qty")
    def _check_received_qty(self):
        for line in self:
            if line.received_qty < 0:
                raise ValidationError(_("Received quantity must be non-negative."))
