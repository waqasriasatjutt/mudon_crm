from odoo import fields, models


class MudonDeveloper(models.Model):
    """Master list of property developers — referenced from WON-stage
    `mudon_developer_id`. Kept as a separate model so the client can
    add / archive developers without code changes."""

    _name = "mudon.developer"
    _description = "Mudon — Property Developer"
    _order = "name"

    name = fields.Char(required=True)
    country = fields.Selection(
        [("turkey", "Turkey"), ("uae", "UAE"), ("both", "Both / Other")],
        default="both",
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)
