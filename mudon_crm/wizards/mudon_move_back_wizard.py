"""Confirmation for moving a card BACKWARD (client comment 3).

    "When moving card back, the selection that moved it advanced is still
     there. We need to think what should we do with the selection for the
     cards that moved backward? I think we should pop out warning message
        Move Card Back?
        This will delete all entries from later stages. Continue?
        Cancel | Move Back"

Backward moves used to be silent, so a card dragged from Meeting back to
Qualified kept ``mudon_visit_confirmed = True`` — which immediately
re-advanced it, or left the milestone ticks lying about a stage the deal
is no longer on. This wizard asks first, then clears every milestone tick
and its stage-owned data for the stages being abandoned.
"""
from odoo import api, fields, models


class MudonMoveBackWizard(models.TransientModel):
    _name = "mudon.move.back.wizard"
    _description = "Mudon — confirm moving a card back a stage"

    lead_id = fields.Many2one(
        "crm.lead", required=True, ondelete="cascade",
    )
    target_stage_id = fields.Many2one(
        "crm.stage", required=True, string="Move back to",
    )
    current_stage_name = fields.Char(
        related="lead_id.stage_id.name", string="Currently on",
    )
    lead_name = fields.Char(related="lead_id.name", string="Lead")
    # Human-readable list of what is about to be wiped, so the user is
    # never guessing what "entries from later stages" means for THIS card.
    cleared_summary = fields.Text(
        compute="_compute_cleared_summary",
        string="This will delete",
    )

    @api.depends("lead_id", "target_stage_id")
    def _compute_cleared_summary(self):
        for wiz in self:
            labels = []
            if wiz.lead_id and wiz.target_stage_id:
                labels = wiz.lead_id._mudon_fields_cleared_on_back(
                    wiz.target_stage_id.mudon_stage_kind, labels_only=True,
                )
            wiz.cleared_summary = (
                "\n".join("• %s" % lbl for lbl in labels)
                if labels
                else self.env._("Nothing — this card has no later-stage "
                                "entries to remove.")
            )

    def action_confirm(self):
        """Clear the abandoned stages' data and write the target stage."""
        self.ensure_one()
        self.lead_id._mudon_move_back_to(self.target_stage_id)
        return {"type": "ir.actions.client", "tag": "soft_reload"}
