"""Quick-fill wizard for stage transitions that need missing data.

Opened via a RedirectWarning button when the user drags a lead card into
a stage whose gate is not the "self-explanatory" auto-tick type. Two
scenarios:

  1. Moving OUT of New Lead with any of MService / MCity / MPriority /
     MBudget unset — the wizard shows only those 4 fields.
  2. Moving to Lost without a Lost Reason — the wizard shows only Lost
     Reason.

The wizard's Confirm button writes the collected values on the lead AND
sets stage_id to the target in a single write, so the after-write hook
runs once and side-effects (notifications, counter bumps) fire normally.
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
    mudon_budget = fields.Monetary(
        string="Budget", currency_field="mudon_budget_currency_id",
    )
    mudon_budget_currency_id = fields.Many2one(
        "res.currency", related="lead_id.mudon_budget_currency_id",
    )
    mudon_lost_reason_id = fields.Many2one(
        "mudon.lost.reason", string="Lost Reason",
    )

    @api.model
    def default_get(self, fields_list):
        """Pre-fill from the lead so partially-filled values aren't
        wiped when the user opens the wizard from a form that already
        has some of them set."""
        res = super().default_get(fields_list)
        lead = self.env["crm.lead"].browse(res.get("lead_id"))
        if lead.exists():
            for fname in (
                "mudon_service_id",
                "mudon_city_id",
                "mudon_priority",
                "mudon_budget",
                "mudon_lost_reason_id",
            ):
                if fname in fields_list and not res.get(fname):
                    val = lead[fname]
                    res[fname] = val.id if hasattr(val, "id") else val
        return res

    def action_confirm(self):
        """Write filled values + target stage on the lead in one write.

        Missing-required-field validation still runs — if the user opens
        the wizard for a New Lead exit and confirms with a field still
        blank, they get a plain UserError (no wizard-inside-wizard).
        """
        self.ensure_one()
        if not self.lead_id or not self.target_stage_id:
            raise UserError(_("Lead or target stage missing on the wizard."))

        kind = self.target_stage_id.mudon_stage_kind
        vals = {}

        if kind and kind not in ("new_lead", "lost"):
            # If moving out of New Lead, enforce the 4 required fields.
            if self.lead_id.mudon_stage_kind_current == "new_lead":
                missing = []
                required_map = {
                    "mudon_service_id": ("Service", self.mudon_service_id),
                    "mudon_city_id": ("City", self.mudon_city_id),
                    "mudon_priority": ("Priority", self.mudon_priority),
                    "mudon_budget": ("Budget", self.mudon_budget),
                }
                for fname, (label, value) in required_map.items():
                    if not value:
                        missing.append(label)
                    else:
                        vals[fname] = value.id if hasattr(value, "id") else value
                if missing:
                    raise UserError(_(
                        "Still missing: %s. Fill them to move the lead forward."
                    ) % ", ".join(missing))

        if kind == "lost":
            if not self.mudon_lost_reason_id:
                raise UserError(_(
                    "Pick a Lost Reason before marking this lead Lost."
                ))
            vals["mudon_lost_reason_id"] = self.mudon_lost_reason_id.id

        vals["stage_id"] = self.target_stage_id.id
        self.lead_id.write(vals)
        # Soft-reload the calling view (kanban) so the card visually
        # moves to its new stage without a full browser refresh.
        return {"type": "ir.actions.client", "tag": "soft_reload"}
