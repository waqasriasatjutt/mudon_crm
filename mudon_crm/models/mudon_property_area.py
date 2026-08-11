from odoo import fields, models


class MudonPropertyArea(models.Model):
    """Area or district the client is interested in — Marina, Downtown,
    Beşiktaş and so on. Many2many on the lead because buyers rarely name
    just one, which is why the client asked for multiple selection.
    """

    _name = "mudon.property.area"
    _description = "Mudon — Property Area"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Stable identifier. Rename `name` freely; leave `code` alone.",
    )
    city_id = fields.Many2one(
        "mudon.city",
        string="City",
        help="Optional. Set it and the form narrows the area list to the "
             "city on the lead.",
    )
    pipeline_kind = fields.Selection(
        [("turkey", "Turkey"), ("uae", "UAE Dubai"), ("any", "Any")],
        default="any", required=True,
        help="Restricts which pipeline this area is offered on.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()

    _code_unique = models.Constraint(
        "unique(code)", "Property-area code must be unique.")
