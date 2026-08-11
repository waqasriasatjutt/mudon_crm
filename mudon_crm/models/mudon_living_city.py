from odoo import fields, models


class MudonLivingCity(models.Model):
    """Where the client currently lives, as opposed to `mudon.city`,
    which is the city they want to BUY in. Kept as its own master so
    the buy-side list stays short (Istanbul, Dubai, ...) while this one
    grows to whatever cities enquiries actually come from.
    """

    _name = "mudon.living.city"
    _description = "Mudon — Living City"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Stable identifier. Rename `name` freely; leave `code` alone.",
    )
    country_id = fields.Many2one(
        "res.country",
        string="Country",
        help="Optional. Set it and the form narrows Living City to the "
             "chosen Living Country.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()

    _code_unique = models.Constraint(
        "unique(code)", "Living-city code must be unique.")
