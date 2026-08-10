from odoo import api, fields, models


class MudonCountryAgentMapping(models.Model):
    """Country-code → sales-agent routing table.

    The CRM auto-assignment hook reads this list in `sequence` order.
    First mapping whose `country_code` matches the lead's phone prefix
    wins. If no mapping matches, the lead falls back to round-robin
    across the team members.
    """

    _name = "mudon.country.agent.mapping"
    _description = "Mudon — Country-Code → Agent Mapping"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    name = fields.Char(
        compute="_compute_name",
        store=True,
    )
    country_code = fields.Char(
        string="Country Code",
        required=True,
        help="Phone-number prefix WITHOUT the leading +. "
             "Example: 966 (Saudi Arabia), 971 (UAE), 90 (Turkey).",
    )
    agent_user_id = fields.Many2one(
        "res.users",
        string="Assigned Agent",
        required=True,
        ondelete="cascade",
    )
    team_id = fields.Many2one(
        "crm.team",
        string="Sales Team",
        help="Optional — restrict this mapping to one pipeline. "
             "Leave empty to apply to all Mudon pipelines.",
    )
    active = fields.Boolean(default=True)

    # Strict unique on (country_code, team_id). Archived rows stay
    # in the table; admin un-archives the existing row rather than
    # creating a duplicate. Including `active` only lets ONE
    # archived row coexist with the live one — second archive
    # collides.
    _country_code_team_unique = models.Constraint(
        "unique(country_code, team_id)",
        "A mapping for this country code already exists on this team.",
    )

    @api.depends("country_code", "agent_user_id")
    def _compute_name(self):
        for rec in self:
            if rec.country_code and rec.agent_user_id:
                rec.name = "+%s → %s" % (
                    rec.country_code, rec.agent_user_id.name,
                )
            else:
                rec.name = rec.country_code or ""
