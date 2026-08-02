"""Mudon org structure + the scoping helpers the record rules rely on.

Client comment 2 — "Where to manage the company structure and who is
reporting to who?" — is answered here rather than by pulling in the whole
HR app: the CRM already knows about teams and branches, so the reporting
line lives next to them and feeds straight into the role-based access
rules (comments 1 + 26).

Two computed helpers back the ir.rule domains in security/mudon_security.xml:

    user.mudon_team_ids    -> every crm.team the user belongs to OR leads
    user.mudon_branch_ids  -> every mudon.branch the user staffs OR manages

Both are non-stored computes with an explicit ``search`` companion so they
stay correct the instant an admin edits a team/branch — no cron, no stale
cache, and no extra column to migrate.
"""
from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # ─── Reporting line (comment 2) ─────────────────────────────────────
    mudon_reports_to_id = fields.Many2one(
        "res.users",
        string="Reports To",
        help="This person's line manager. Drives the Company Structure "
             "report under Mudon CRM → Configuration → Company Structure.",
    )
    mudon_direct_report_ids = fields.One2many(
        "res.users", "mudon_reports_to_id",
        string="Direct Reports",
    )
    mudon_direct_report_count = fields.Integer(
        compute="_compute_mudon_direct_report_count",
        string="Direct Reports",
    )
    mudon_job_title = fields.Char(
        string="Job Title",
        help="Free-text title shown on the Company Structure list "
             "(e.g. 'Branch Manager — Dubai').",
    )

    @api.depends("mudon_direct_report_ids")
    def _compute_mudon_direct_report_count(self):
        for user in self:
            user.mudon_direct_report_count = len(user.mudon_direct_report_ids)

    @api.constrains("mudon_reports_to_id")
    def _check_mudon_reporting_loop(self):
        """A reporting line must be a tree, not a ring."""
        for user in self:
            seen = set()
            node = user.mudon_reports_to_id
            while node:
                if node.id in seen or node.id == user.id:
                    from odoo.exceptions import ValidationError
                    raise ValidationError(self.env._(
                        "That reporting line loops back on itself. "
                        "A user cannot end up reporting to themselves."
                    ))
                seen.add(node.id)
                node = node.mudon_reports_to_id

    # ─── Scoping helpers used by the record rules ───────────────────────
    mudon_team_ids = fields.Many2many(
        "crm.team",
        string="Mudon Teams (member or leader)",
        compute="_compute_mudon_scope",
        search="_search_mudon_team_ids",
        help="Every sales team this user is a member of, plus any team "
             "they are the manager of. Used by the Team Leader access rule.",
    )
    mudon_branch_ids = fields.Many2many(
        "mudon.branch",
        string="Mudon Branches (agent or manager)",
        compute="_compute_mudon_scope",
        search="_search_mudon_branch_ids",
        help="Every branch this user staffs, plus any branch they manage. "
             "Used by the Branch Manager access rule.",
    )

    def _compute_mudon_scope(self):
        Team = self.env["crm.team"].sudo()
        Branch = self.env["mudon.branch"].sudo()
        for user in self:
            user.mudon_team_ids = Team.search([
                "|", ("member_ids", "in", user.id), ("user_id", "=", user.id),
            ])
            user.mudon_branch_ids = Branch.search([
                "|", ("member_ids", "in", user.id),
                ("manager_id", "=", user.id),
            ])

    def _search_mudon_team_ids(self, operator, value):
        teams = self.env["crm.team"].sudo().browse(
            value if isinstance(value, (list, tuple)) else [value])
        uids = set(teams.member_ids.ids) | set(teams.user_id.ids)
        return [("id", "in" if operator in ("in", "=") else "not in", list(uids))]

    def _search_mudon_branch_ids(self, operator, value):
        branches = self.env["mudon.branch"].sudo().browse(
            value if isinstance(value, (list, tuple)) else [value])
        uids = set(branches.member_ids.ids) | set(branches.manager_id.ids)
        return [("id", "in" if operator in ("in", "=") else "not in", list(uids))]

    # ─── Company-structure report ───────────────────────────────────────
    def action_mudon_open_direct_reports(self):
        """Stat button → the people reporting into this user."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Direct Reports — %s", self.name),
            "res_model": "res.users",
            "domain": [("mudon_reports_to_id", "=", self.id)],
            "views": [[self.env.ref(
                "mudon_crm.mudon_res_users_view_list_structure").id, "list"],
                [False, "form"]],
            "target": "current",
            "context": {"default_mudon_reports_to_id": self.id},
        }
