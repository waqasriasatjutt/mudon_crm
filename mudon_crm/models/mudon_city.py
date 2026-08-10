from odoo import fields, models


class MudonCity(models.Model):
    """City tags for the lead form. Many2many on crm.lead, so a
    customer can express interest in several at once (Istanbul OR
    Trabzon). Branch routing matches the FIRST city tag whose `code`
    matches a `mudon.branch.city_key`.
    """

    _name = "mudon.city"
    _description = "Mudon — City"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Stable identifier (e.g. 'istanbul', 'dubai'). Must match "
             "the `city_key` on `mudon.branch` for routing to work.",
    )
    pipeline_kind = fields.Selection(
        [("turkey", "Turkey"), ("uae", "UAE Dubai"), ("any", "Any")],
        default="any",
        help="Restricts which pipeline this city is offered on.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()

    _code_unique = models.Constraint(
        "unique(code)", "City code must be unique.")
