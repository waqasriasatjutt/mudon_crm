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

    _sql_constraints = [
        ("code_unique", "unique(code)", "Purpose code must be unique."),
    ]
