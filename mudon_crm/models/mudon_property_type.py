from odoo import fields, models


class MudonPropertyType(models.Model):
    """Property type tags — customer often browses multiple
    (Apartment OR Villa). Many2many on the lead.
    """

    _name = "mudon.property.type"
    _description = "Mudon — Property Type"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()

    _sql_constraints = [
        ("code_unique", "unique(code)", "Property-type code must be unique."),
    ]
