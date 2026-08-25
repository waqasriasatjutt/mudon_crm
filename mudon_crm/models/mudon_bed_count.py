from odoo import fields, models


class MudonBedCount(models.Model):
    """Bedroom counts a client will consider.

    A list rather than a single value because buyers rarely want exactly
    one size: "2 or 3 bedrooms" is the normal answer. The client asked for
    it to behave like Property Type, which is the same idea.
    """

    _name = "mudon.bed.count"
    _description = "Mudon — Number of Beds"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Stable identifier. Rename `name` freely; leave `code` alone.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()

    _code_unique = models.Constraint(
        "unique(code)", "Bed-count code must be unique.")
