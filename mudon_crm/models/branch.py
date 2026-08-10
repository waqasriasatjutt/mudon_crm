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
