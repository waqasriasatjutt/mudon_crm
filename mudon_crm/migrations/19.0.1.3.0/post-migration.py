"""Post-migration for mudon_crm 19.0.1.3.0.

The free-text WON "Project Name" (mudon_project_name, Char) became a
managed master: mudon.project + mudon_project_id (Many2one). Create one
project per distinct legacy value and re-link the leads. The old varchar
column still exists when this runs (Odoo drops obsolete columns only at
the very end of the upgrade), so we can read it.

Budget currency became a stored COMPUTE (UAE=AED, Turkey=USD). Odoo does
NOT auto-recompute a stored field that already has values when a plain
column is turned into a computed one on upgrade, so we force the recompute
here for every Mudon lead (otherwise existing UAE leads would keep their
old USD value). base.AED is already active by now (res_currency_data.xml
loads during module init, before this post-migration).

The lead re-link uses raw SQL on purpose — going through the ORM would
fire the crm.lead write() override (stage-transition gates, WhatsApp
hooks) which must not run during a data migration.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = %s AND column_name = %s""",
        (table, column),
    )
    return bool(cr.fetchone())


def _recompute_budget_currency(env):
    """mudon_budget_currency_id became a stored compute this release.
    Odoo does not recompute a stored field that already holds values when
    a plain column becomes computed on upgrade, so existing UAE leads would
    keep USD. Force it for every Mudon lead."""
    leads = env["crm.lead"].search(
        [("mudon_pipeline_kind", "in", ("turkey", "uae"))]
    )
    if not leads:
        return
    leads._compute_mudon_budget_currency()
    leads.flush_recordset(["mudon_budget_currency_id"])
    _logger.info("mudon_crm 1.3.0: recomputed budget currency for %s lead(s)",
                 len(leads))


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Always force the per-pipeline currency recompute (independent of the
    # project backfill below).
    _recompute_budget_currency(env)

    if not _column_exists(cr, "crm_lead", "mudon_project_name"):
        return
    cr.execute(
        """SELECT DISTINCT btrim(mudon_project_name)
             FROM crm_lead
            WHERE mudon_project_name IS NOT NULL
              AND btrim(mudon_project_name) <> ''"""
    )
    names = [r[0] for r in cr.fetchall()]
    if not names:
        return

    Project = env["mudon.project"]
    name_to_id = {}
    for name in names:
        proj = Project.search([("name", "=", name)], limit=1) \
            or Project.create({"name": name})
        name_to_id[name] = proj.id
    env.flush_all()

    for name, pid in name_to_id.items():
        cr.execute(
            """UPDATE crm_lead SET mudon_project_id = %s
                WHERE btrim(mudon_project_name) = %s
                  AND mudon_project_id IS NULL""",
            (pid, name),
        )
        _logger.info("mudon_crm 1.3.0: project %r -> id %s linked to %s lead(s)",
                     name, pid, cr.rowcount)
