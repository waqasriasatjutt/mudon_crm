from odoo import api, fields, models


class MudonBranch(models.Model):
    """A city-level branch that owns a roster of agents.

    Stage 2+ routes leads from `crm.lead.mudon_city_id` → matching
    `mudon.branch` → round-robin across `member_ids`. If no branch
    matches the lead's city ("Others" in the spec), the lead falls
    back to the team's Sales Manager (`team_id.user_id`).
    """

    _name = "mudon.branch"
    _description = "Mudon — City Branch"
    _order = "team_id, sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    team_id = fields.Many2one(
        "crm.team",
        required=True,
        ondelete="cascade",
        string="Sales Team / Pipeline",
    )
    city_key = fields.Selection(
        [
            ("istanbul", "Istanbul"),
            ("trabzon", "Trabzon"),
            ("dubai", "Dubai"),
            ("abudhabi", "Abu Dhabi"),
            ("sharjah", "Sharjah"),
            ("rak", "Ras Al Khaimah"),
        ],
        required=True,
        help="Matches a `mudon.city.code` on the lead's `mudon_city_id`. "
             "Each city key may bind to at most one branch per team.",
    )
    member_ids = fields.Many2many(
        "res.users",
        "mudon_branch_user_rel",
        "branch_id", "user_id",
        string="Branch Agents",
    )
    manager_id = fields.Many2one(
        "res.users",
        string="Branch Manager",
        help="Optional. Receives some escalation messages alongside "
             "the team Sales Manager.",
    )
    active = fields.Boolean(default=True)

    # Strict unique on (team, city). Archiving keeps the row in
    # the table; admin un-archives the existing one rather than
    # creating a duplicate. Including `active` in the unique
    # tuple only allows ONE archived row per (team, city) pair —
    # second archive collides.
    _team_city_unique = models.Constraint(
        "unique(team_id, city_key)",
        "A branch for this city already exists on this team.",
    )

    def _mudon_refresh_lead_branches(self):
        self._mudon_refresh_lead_branches_for(
            self.with_context(active_test=False).mapped("team_id"))

    @api.model
    def _mudon_refresh_lead_branches_for(self, teams):
        """Recompute `crm.lead.mudon_branch_id` for the teams we touched.

        The lead's branch is a STORED compute that depends only on the
        lead's own team and city, so nothing tells it that a branch was
        added, archived or moved to another city. Leads created before a
        new branch existed kept `mudon_branch_id = False` forever, which
        meant opening a new office quietly did nothing for the existing
        backlog and the manager's "Re-run automatic assignment" could not
        repair it either.
        """
        if not teams:
            return
        leads = self.env["crm.lead"].sudo().with_context(
            active_test=False).search([("team_id", "in", teams.ids)])
        if leads:
            self.env.add_to_compute(
                leads._fields["mudon_branch_id"], leads)

    @api.model_create_multi
    def create(self, vals_list):
        branches = super().create(vals_list)
        branches._mudon_refresh_lead_branches()
        return branches

    def write(self, vals):
        # Collect the team the branch is moving AWAY from as well as the one
        # it lands on, so leads on both sides are re-evaluated.
        before = self.with_context(active_test=False).mapped("team_id")
        res = super().write(vals)
        after = self.with_context(active_test=False).mapped("team_id")
        self._mudon_refresh_lead_branches_for(before | after)
        return res

    def unlink(self):
        teams = self.with_context(active_test=False).mapped("team_id")
        res = super().unlink()
        self._mudon_refresh_lead_branches_for(teams)
        return res

    def _pick_next_agent(self, exclude_lead_id=False):
        """Round-robin within this branch. Picks the member who comes
        AFTER the most-recently-assigned member on the branch's team.
        Falls back to `None` if the branch has no members."""
        self.ensure_one()
        members = self.member_ids.sorted("id")
        if not members:
            return self.env["res.users"]
        Lead = self.env["crm.lead"].sudo()
        domain = [
            ("team_id", "=", self.team_id.id),
            ("user_id", "in", members.ids),
        ]
        if exclude_lead_id:
            domain.append(("id", "!=", exclude_lead_id))
        last = Lead.search(domain, order="id desc", limit=1)
        if not last or last.user_id not in members:
            return members[0]
        idx = list(members).index(last.user_id)
        return members[(idx + 1) % len(members)]
