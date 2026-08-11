from odoo import fields, models


class MudonCommissionType(models.Model):
    """How the commission on a won deal is taken — cash or invoiced.

    A master rather than a Selection because the client asked for it
    under Configuration, and because "Cash / Invoice" is the sort of
    list that grows once finance starts using it.
    """

    _name = "mudon.commission.type"
    _description = "Mudon — Commission Type"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Stable identifier. Rename `name` freely; leave `code` alone.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()

    _code_unique = models.Constraint(
        "unique(code)", "Commission-type code must be unique.")
