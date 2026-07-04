from . import models
from . import controllers
from . import wizards


def post_init_hook(env):
    """Activate AED on fresh install — the UAE Dubai pipeline bills in AED.
    A base currency's xmlid is noupdate, so it can't be flipped from a data
    file; do it in code instead. (Upgrades are handled by the matching
    migration script.)"""
    aed = env.ref("base.AED", raise_if_not_found=False)
    if aed and not aed.active:
        aed.active = True
    _mudon_cleanup_default_stages(env)


def _mudon_cleanup_default_stages(env):
    """Remove Odoo's stock CRM stages (New / Qualified / Proposition /
    Won) so a Mudon-only tenant's pipeline shows ONLY the per-team Mudon
    stages — and new leads start on 'New Lead' instead of Odoo's "New"
    (which has no Mudon stage-kind, so the current-stage fields wouldn't
    show). Any lead still parked on a stock stage is moved to its team's
    New Lead first. Idempotent — safe to re-run on every upgrade.

    "Stock" = a crm.stage with no mudon_stage_kind; every Mudon stage
    carries one, so this targets exactly the leftover Odoo defaults."""
    Stage = env["crm.stage"]
    defaults = Stage.search([("mudon_stage_kind", "=", False)])
    if not defaults:
        return
    Lead = env["crm.lead"].with_context(active_test=False)
    for lead in Lead.search([("stage_id", "in", defaults.ids)]):
        target = Stage.search([
            ("mudon_stage_kind", "=", "new_lead"),
            ("team_ids", "in", lead.team_id.ids),
        ], limit=1) or Stage.search(
            [("mudon_stage_kind", "=", "new_lead")], limit=1,
        )
        if target:
            lead.with_context(mudon_in_write=True).write(
                {"stage_id": target.id})
    defaults.unlink()
