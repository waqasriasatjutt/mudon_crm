"""Quick-fill wizard for stage transitions that need missing data.

Opened via a RedirectWarning button when a drag needs data the target
stage requires. It shows the mandatory field(s) for EVERY funnel stage
crossed by the drag (only those, driven by the mudon_need_* flags):

  1. Moving OUT of New Lead — Service / City / Priority / Budget.
  2. Skipping stages (e.g. New → Meeting) — the above PLUS a confirm
     checkbox for each crossed milestone (Offer Sent, Visit Confirmed…).
  3. Moving to Lost — only the Lost Reason.

The Confirm button writes the collected values on the lead AND sets
stage_id to the target in a single write, so the after-write hook runs
once and side-effects (notifications, counter bumps) fire normally.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MudonQuickFillWizard(models.TransientModel):
    _name = "mudon.quick.fill.wizard"
    _description = "Mudon — Quick-fill for stage transitions"

    lead_id = fields.Many2one(
        "crm.lead", required=True, ondelete="cascade",
    )
    target_stage_id = fields.Many2one(
        "crm.stage", required=True,
        string="Target Stage",
    )
    target_stage_kind = fields.Selection(
        related="target_stage_id.mudon_stage_kind", store=False,
    )
    pipeline_kind = fields.Selection(
        related="lead_id.mudon_pipeline_kind", store=False,
    )

    # Fields that may need to be filled — visibility driven by
    # target_stage_kind in the form view.
    mudon_service_id = fields.Many2one(
        "mudon.service", string="Service",
    )
    mudon_city_id = fields.Many2one(
        "mudon.city", string="City",
    )
    mudon_priority = fields.Selection(
        [("urgent", "Urgent"), ("normal", "Normal")],
        string="Priority",
    )
    # Follow the LEAD's pipeline currency, not the company's. A Turkey
    # lead is quoted in USD and a Dubai one in AED, so billing the wizard
    # against the company currency showed the wrong symbol on one of the
    # two pipelines whichever way the company was set up.
    expected_revenue = fields.Monetary(
        string="Expected Revenue",
        currency_field="mudon_budget_currency_id",
    )
    mudon_budget_currency_id = fields.Many2one(
        "res.currency", related="lead_id.mudon_budget_currency_id",
    )
    mudon_lost_reason_id = fields.Many2one(
        "mudon.lost.reason", string="Lost Reason",
    )

    # Per-stage confirmations — shown when the drag SKIPS the stage they
    # belong to, so the user confirms each crossed milestone.
    mudon_tick_offer_sent = fields.Boolean(string="Offer Sent")
    mudon_visit_confirmed = fields.Boolean(string="Visit Confirmed")
    # Required alongside the tick when a card moves to Meeting. Without it the
    # loop below reads a field the wizard does not have and the whole dialog
    # dies with KeyError: 'mudon_visit_date'.
    mudon_visit_date = fields.Date(string="Expected Visit Date")
    mudon_paid_booking = fields.Boolean(string="Paid Booking")
    mudon_fully_paid = fields.Boolean(string="Fully Paid")

    # Won-stage deal data (client 09-16): forced when a card moves to Win.
    # Developer / Project / Handover are open to any agent; Closing Amount and
    # Commission carry the same financial-data restriction as the lead itself
    # (comment 12), so a non-financial user never sees or sets them here either.
    mudon_developer_id = fields.Many2one("mudon.developer", string="Developer")
    mudon_project_id = fields.Many2one("mudon.project", string="Project")
    mudon_handover_type = fields.Selection(
        [("ready", "Ready"), ("off_plan", "Off-Plan")], string="Handover Type")
    mudon_closing_amount = fields.Monetary(
        string="Closing Amount", currency_field="mudon_budget_currency_id",
        groups="mudon_crm.group_mudon_financial_data")
    mudon_commission_pct = fields.Float(
        string="Commission %", digits=(5, 2),
        groups="mudon_crm.group_mudon_financial_data")

    # Which crossed stages this transition needs — drives form
    # visibility so only the relevant fields show.
    mudon_need_qualify = fields.Boolean(compute="_compute_mudon_needs")
    mudon_need_offer = fields.Boolean(compute="_compute_mudon_needs")
    mudon_need_visit = fields.Boolean(compute="_compute_mudon_needs")
    mudon_need_booking = fields.Boolean(compute="_compute_mudon_needs")
    mudon_need_paid = fields.Boolean(compute="_compute_mudon_needs")
    mudon_need_won = fields.Boolean(compute="_compute_mudon_needs")
    mudon_has_financial = fields.Boolean(compute="_compute_mudon_needs")

    @api.depends("lead_id", "target_stage_id")
    def _compute_mudon_needs(self):
        for wiz in self:
            crossed = []
            if wiz.lead_id and wiz.target_stage_id:
                crossed = wiz.lead_id._mudon_crossed_required(
                    wiz.lead_id.mudon_stage_kind_current,
                    wiz.target_stage_id.mudon_stage_kind,
                )
            wiz.mudon_need_qualify = "mudon_service_id" in crossed
            wiz.mudon_need_offer = "mudon_tick_offer_sent" in crossed
            wiz.mudon_need_visit = "mudon_visit_confirmed" in crossed
            wiz.mudon_need_booking = "mudon_paid_booking" in crossed
            wiz.mudon_need_paid = "mudon_fully_paid" in crossed
            wiz.mudon_need_won = "mudon_developer_id" in crossed
            wiz.mudon_has_financial = wiz.env.user.has_group(
                "mudon_crm.group_mudon_financial_data")

    @api.model
    def default_get(self, fields_list):
        """Pre-fill from the lead so partially-filled values aren't
        wiped when the user opens the wizard from a form that already
        has some of them set."""
        res = super().default_get(fields_list)
        lead = self.env["crm.lead"].browse(res.get("lead_id"))
        if lead.exists():
            # Pre-fill DATA fields only (don't re-type a Budget already
            # set). The milestone ticks are deliberately NOT pre-filled —
            # each skipped step must be freshly confirmed by the user.
            for fname in (
                "mudon_service_id",
                "mudon_city_id",
                "mudon_priority",
                "expected_revenue",
                "mudon_lost_reason_id",
                "mudon_developer_id",
                "mudon_project_id",
                "mudon_handover_type",
            ):
                if fname in fields_list and not res.get(fname):
                    val = lead[fname]
                    res[fname] = val.id if hasattr(val, "id") else val
            # The two money fields are financial-restricted: only read them
            # off the lead for a user who is allowed to see them, or the
            # prefill itself raises AccessError.
            if self.env.user.has_group("mudon_crm.group_mudon_financial_data"):
                for fname in ("mudon_closing_amount", "mudon_commission_pct"):
                    if fname in fields_list and not res.get(fname):
                        res[fname] = lead[fname]
        return res

    def action_confirm(self):
        """Write every crossed stage's mandatory field + the target stage
        on the lead in ONE write.

        Validates each required field is filled / confirmed first, so the
        user gets a plain message (no wizard-inside-wizard). For a skip
        (e.g. New → Meeting) this collects Qualified + Offer Sent +
        Meeting together; for Lost it collects only the Lost Reason.
        """
        self.ensure_one()
        lead = self.lead_id
        if not lead or not self.target_stage_id:
            raise UserError(_("Lead or target stage missing on the wizard."))

        target_kind = self.target_stage_id.mudon_stage_kind
        vals = {}
        missing = []

        if target_kind == "lost":
            if not self.mudon_lost_reason_id:
                missing.append(_("Lost Reason"))
            else:
                vals["mudon_lost_reason_id"] = self.mudon_lost_reason_id.id
        else:
            crossed = lead._mudon_crossed_required(
                lead.mudon_stage_kind_current, target_kind,
            )
            input_labels = {
                "mudon_service_id": _("Service"),
                "mudon_city_id": _("City"),
                "mudon_priority": _("Priority"),
                "expected_revenue": _("Expected Revenue"),
                "mudon_visit_date": _("Expected Visit Date"),
                # Won-stage plain fields, open to any agent.
                "mudon_developer_id": _("Developer"),
                "mudon_project_id": _("Project"),
                "mudon_handover_type": _("Handover Type"),
            }
            tick_labels = {
                "mudon_tick_offer_sent": _("Offer Sent"),
                "mudon_visit_confirmed": _("Visit Confirmed"),
                "mudon_paid_booking": _("Paid Booking"),
                "mudon_fully_paid": _("Fully Paid"),
            }
            # Closing Amount and Commission are financial-restricted, so a user
            # without Financial Data Access cannot set them and therefore cannot
            # close a deal. Say so plainly instead of letting the write blow up
            # with an AccessError. Reading `self[money_field]` is only safe once
            # we know the user holds the group, so this guard runs first.
            money_labels = {
                "mudon_closing_amount": _("Closing Amount"),
                "mudon_commission_pct": _("Commission %"),
            }
            if any(f in money_labels for f in crossed) and \
                    not self.mudon_has_financial:
                raise UserError(_(
                    "Closing a deal records the Closing Amount and Commission, "
                    "which need Financial Data Access. Ask a manager with that "
                    "access to complete the win."))
            for fname in crossed:
                # A stage requirement the wizard has no field for used to raise
                # KeyError and take the whole dialog down. Skipping it keeps the
                # move working; the model still enforces the requirement on
                # write, so nothing slips through.
                if fname not in self._fields:
                    continue
                if fname in money_labels:
                    value = self[fname]   # safe: has-financial checked above
                    if not value:
                        missing.append(money_labels[fname])
                    else:
                        vals[fname] = value
                    continue
                value = self[fname]
                if fname in input_labels:
                    if not value:
                        missing.append(input_labels[fname])
                    else:
                        vals[fname] = value.id if hasattr(value, "id") else value
                elif fname in tick_labels:
                    if not value:
                        missing.append(tick_labels[fname])
                    else:
                        vals[fname] = True

        if missing:
            raise UserError(
                _("Please complete / confirm: %s") % ", ".join(missing)
            )

        vals["stage_id"] = self.target_stage_id.id
        lead.write(vals)
        # Soft-reload the calling view (kanban) so the card visually
        # moves to its new stage without a full browser refresh.
        return {"type": "ir.actions.client", "tag": "soft_reload"}
