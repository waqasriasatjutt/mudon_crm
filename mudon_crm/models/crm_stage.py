from odoo import api, fields, models


MUDON_STAGE_KIND_SELECTION = [
    ("new_lead", "New Lead"),
    ("qualified", "Qualified"),
    ("offer_sent", "Offer Sent"),
    ("meeting", "Meeting"),
    ("eoi", "EOI / Booking"),
    ("won", "WON (SPA Signed)"),
    ("lost", "Lost"),
]


class CrmStage(models.Model):
    """Tag every stage with a semantic kind so transitions, SLA crons
    and automation logic across the module reference `stage_id.kind ==
    'offer_sent'` instead of `env.ref('mudon_crm.mudon_stage_turkey_
    offer_sent')`.

    Adding a new Mudon-style pipeline (e.g. KSA) then only means
    seeding stages with the right `mudon_stage_kind` — no code
    changes anywhere else in the module.
    """

    _inherit = "crm.stage"

    mudon_stage_kind = fields.Selection(
        MUDON_STAGE_KIND_SELECTION,
        string="Mudon Stage Kind",
        help="Semantic role of this stage within a Mudon pipeline. "
             "Drives stage-transition writes, SLA crons, and the "
             "kanban color-coding logic. Leave empty for non-Mudon "
             "stages.",
        index=True,
    )

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        """Scope stage lookups to the pipeline the user is working in.

        Client comment 7 — "Filter is not working correctly, when filtering
        stage while in Dubai pipeline, it shows all the item of both
        pipelines". His screenshot is the Search: Stage picker listing all
        14 stages, reached through Add Custom Filter. That dialog builds a
        raw domain, so no search-view attribute can reach it; the only place
        to scope it is the stage model itself.

        Both Mudon pipeline actions carry their team in ``default_team_id``,
        so that is the signal. The scope is applied only when the team
        genuinely owns stages, which keeps every non-Mudon pipeline, the SLA
        crons and ``_read_group_stage_ids`` behaving exactly as before.
        """
        team_id = self.env.context.get("default_team_id")
        if team_id and not self.env.context.get("mudon_all_stages"):
            try:
                team_id = int(team_id)
            except (TypeError, ValueError):
                team_id = None
            if team_id:
                owns_stages = super()._search(
                    [("team_ids", "=", team_id)], limit=1)
                if owns_stages:
                    domain = list(domain or []) + [
                        ("team_ids", "=", team_id)]
        return super()._search(
            domain, offset=offset, limit=limit, order=order, **kwargs)
