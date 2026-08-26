from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("country_code"):
                vals["country_code"] = self._mudon_clean_code(vals["country_code"])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("country_code"):
            vals["country_code"] = self._mudon_clean_code(vals["country_code"])
        return super().write(vals)

    @staticmethod
    def _mudon_clean_code(code):
        """Keep only the digits of a dialling code.

        Routing compares this against the digits of the lead's phone, so a
        code saved as "+966" or "00966" could never match anything and the
        rule silently did nothing. Writing the plus is the natural way for
        a person to type a dialling code, so accept it and store what the
        matching actually needs.
        """
        digits = "".join(ch for ch in (code or "") if ch.isdigit())
        return digits.lstrip("0") or digits

    # Strict unique on (country_code, team_id). Archived rows stay
    # in the table; admin un-archives the existing row rather than
    # creating a duplicate. Including `active` only lets ONE
    # archived row coexist with the live one — second archive
    # collides.
    _country_code_team_unique = models.Constraint(
        "unique(country_code, team_id)",
        "A mapping for this country code already exists on this team.",
    )

    @api.constrains("country_code", "team_id", "active")
    def _check_mudon_unique_code(self):
        """Reject a second live mapping for the same code and team.

        The SQL constraint above cannot police the team-less rows: Postgres
        treats every NULL as distinct, so two global mappings for 971 were
        both accepted. Routing takes the first with `limit=1`, so the second
        one silently never applied and the admin had no way to tell which
        of the two was in force.
        """
        for rec in self:
            if not rec.active or not rec.country_code:
                continue
            clash = self.search([
                ("id", "!=", rec.id),
                ("country_code", "=", rec.country_code),
                ("team_id", "=", rec.team_id.id),
            ], limit=1)
            if clash:
                scope = (rec.team_id.name if rec.team_id
                         else "all Mudon pipelines")
                raise ValidationError(_(
                    "There is already a mapping for +%(code)s on "
                    "%(scope)s, sending leads to %(agent)s. Edit that one "
                    "instead of adding a second — only the first would "
                    "ever be used.",
                    code=rec.country_code, scope=scope,
                    agent=clash.agent_user_id.name,
                ))

    @api.depends("country_code", "agent_user_id")
    def _compute_name(self):
        for rec in self:
            if rec.country_code and rec.agent_user_id:
                rec.name = "+%s → %s" % (
                    rec.country_code, rec.agent_user_id.name,
                )
            else:
                rec.name = rec.country_code or ""
