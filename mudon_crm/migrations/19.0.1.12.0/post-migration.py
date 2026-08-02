"""Upgrade to 19.0.1.12.0 — the 32-point client feedback round.

Handles the parts that are DATA rather than code:

  * comment 13 — strip Odoo's "<Contact>'s opportunity" wording that is
    stored in crm_lead.name, not rendered by the view;
  * comment 29 — take "Generate Leads" out of the pipeline toolbar;
  * comments 18 + 27 — populate the new per-stage kanban sort key;
  * comments 1 + 26 — give existing users a Mudon role so nobody loses
    access the moment the record rules switch on;
  * comments 14/15/16 — flag how many leads are still sitting on the
    creating user because auto-routing never ran.

Every step is idempotent and guarded, so a re-run is harmless.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    _strip_opportunity_suffix(cr)
    _hide_generate_leads(env)
    _recompute_sort_keys(env)
    _seed_roles(env)
    _report_unrouted_leads(env)


def _strip_opportunity_suffix(cr):
    """Comment 13 — "Remove word opportunity from all cards in both
    pipelines". Odoo names an opportunity created from a contact
    "<Contact>'s opportunity"; that text lives in the column, so hiding it
    in the view is not possible. Rewrite it in place, both the straight
    apostrophe and the typographic one."""
    cr.execute(
        """
        UPDATE crm_lead
           SET name = btrim(regexp_replace(name, '(''|’)s opportunity$', ''))
         WHERE mudon_pipeline_kind IS NOT NULL
           AND name ~ '(''|’)s opportunity$'
           AND btrim(regexp_replace(name, '(''|’)s opportunity$', '')) <> ''
        """
    )
    _logger.info("mudon_crm: cleaned 's opportunity' from %s lead name(s)",
                 cr.rowcount)


def _hide_generate_leads(env):
    """Comment 29 — "Remove generate leads from top".

    The button belongs to Odoo's `crm_iap_mine` app (paid lead-mining
    credits), which Mudon does not use. Its two kanban inherits are
    ARCHIVED rather than deleted, so the change is a one-click revert
    (set active = True) if the client ever buys into the service.
    """
    for xmlid in (
        "crm_iap_mine.crm_case_kanban_view_leads",
        "crm_iap_mine.view_crm_lead_kanban",
        "crm_iap_mine.crm_lead_view_tree_opportunity",
        "crm_iap_mine.crm_lead_view_tree_lead",
    ):
        view = env.ref(xmlid, raise_if_not_found=False)
        if view and view.active:
            view.active = False
            _logger.info("mudon_crm: archived %s (Generate Leads button)",
                         xmlid)


def _recompute_sort_keys(env):
    """Comments 18 + 27 — populate `mudon_sort_key` for existing rows.

    Odoo computes a newly-added stored field during the upgrade, but the
    key depends on `mudon_stage_kind_current`, which is itself a stored
    related. Forcing the recompute here guarantees the columns are ordered
    correctly the first time the client opens the board rather than after
    the next write.
    """
    leads = env["crm.lead"].with_context(active_test=False).search([])
    if leads:
        env.add_to_compute(env["crm.lead"]._fields["mudon_sort_key"], leads)
        leads.flush_recordset(["mudon_sort_key"])
    _logger.info("mudon_crm: recomputed kanban sort key for %s lead(s)",
                 len(leads))


def _seed_roles(env):
    """Comments 1 + 26 — put existing users into the new role groups.

    The record rules go live with this upgrade, so anyone left without a
    Mudon role would suddenly be scoped by whatever stock groups they hold.
    Map them from the Odoo sales groups they already have, keeping today's
    effective access intact:

        Sales Manager  -> Super Admin   (unrestricted, as they are now)
        Salesperson    -> Sales Agent   (own records, per the matrix)

    Admins are handled first so the client can never lock themselves out.
    """
    super_admin = env.ref("mudon_crm.group_mudon_super_admin",
                          raise_if_not_found=False)
    agent = env.ref("mudon_crm.group_mudon_sales_agent",
                    raise_if_not_found=False)
    if not (super_admin and agent):
        return

    sale_manager = env.ref("sales_team.group_sale_manager",
                           raise_if_not_found=False)
    salesman = env.ref("sales_team.group_sale_salesman",
                       raise_if_not_found=False)
    system = env.ref("base.group_system", raise_if_not_found=False)

    Users = env["res.users"].with_context(active_test=False)
    internal = Users.search([("share", "=", False)])

    for user in internal:
        groups = user.group_ids
        already = super_admin in groups or agent in groups
        if already:
            continue
        is_admin = (
            (system and system in groups)
            or (sale_manager and sale_manager in groups)
            or user.id == env.ref("base.user_admin").id
        )
        target = super_admin if is_admin else (
            agent if salesman and salesman in groups else None)
        if target:
            user.sudo().write({"group_ids": [(4, target.id)]})
            _logger.info("mudon_crm: %s -> %s", user.login, target.name)


def _report_unrouted_leads(env):
    """Comments 14/15/16 — surface the backlog the routing bug created.

    Auto-assignment used to bail out whenever `user_id` was set, and Odoo
    always defaults it to the creating user, so routing never ran for
    UI-created leads. Existing leads are NOT reassigned automatically here
    (that would move live work between agents without warning); the count
    is logged, and managers can select those leads and run
    Actions -> Re-run automatic assignment when they choose to.
    """
    leads = env["crm.lead"].search([
        ("mudon_pipeline_kind", "!=", False),
        ("user_id.login", "in", ("admin", "__system__")),
    ])
    if leads:
        _logger.warning(
            "mudon_crm: %s Mudon lead(s) are still assigned to an admin "
            "account because auto-routing never ran. Select them in the "
            "pipeline and use Actions > Re-run automatic assignment.",
            len(leads),
        )
