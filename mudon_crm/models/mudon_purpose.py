from odoo import fields, models


class MudonPurpose(models.Model):
    """Purpose-of-property tags. Many2many on the lead so a customer
    can declare multiple purposes (e.g. Citizenship + Investment).
    """

    _name = "mudon.purpose"
    _description = "Mudon — Purpose of Property"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    pipeline_kind = fields.Selection(
        [("turkey", "Turkey"), ("uae", "UAE Dubai"), ("any", "Any")],
        default="any", required=True,
        help="Which pipeline offers this purpose. Turkey = Citizenship; "
             "UAE Dubai = Golden Visa; the rest = Any (both). The lead "
             "form filters Purpose by the lead's pipeline.",
    )

    _code_unique = models.Constraint(
        "unique(code)", "Purpose code must be unique.")
