from odoo import fields, models


class MudonSource(models.Model):
    """Lead source master. Many2one on the lead (single origination
    channel). Admin adds new ones as marketing channels evolve.

    `channel` separates the System-attribution sources (META, Google
    Ads, Bayut, etc. — typically set by an inbound integration) from
    Manual sources picked by agents at lead creation. Pure metadata
    for filtering / reporting; doesn't change automation behaviour.
    """

    _name = "mudon.source"
    _description = "Mudon — Lead Source"
    _order = "channel, sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    channel = fields.Selection(
        [("system", "System (auto-attributed)"),
         ("manual", "Manual (agent picks)")],
        default="manual",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()

    _sql_constraints = [
        ("code_unique", "unique(code)", "Source code must be unique."),
    ]
