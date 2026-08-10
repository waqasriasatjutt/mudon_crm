from odoo import fields, models


class MudonService(models.Model):
    """Service catalog managed via Configuration → Services.

    `code` is the stable identifier the Python automation logic looks up
    (e.g. `rec.mudon_service_id.code == "citizenship"`). Admin can rename
    `name` without breaking any rule. Don't change `code` on seeded
    records — that's the join point with the kanban color logic and the
    sort priority ranking.
    """

    _name = "mudon.service"
    _description = "Mudon — Service Type"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Stable identifier used by the module's automation rules "
             "(kanban color, sort priority). Do not change on seeded "
             "records like 'citizenship', 'investment', 'goldenvisa'.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    pipeline_kind = fields.Selection(
        [("turkey", "Turkey"), ("uae", "UAE Dubai"), ("any", "Any")],
        default="any", required=True,
        help="Which pipeline offers this service. Turkey = Citizenship; "
             "UAE Dubai = Golden Visa; Investment = Any (both). The lead "
             "form filters MService by the lead's pipeline.",
    )

    _code_unique = models.Constraint(
        "unique(code)", "Service code must be unique.")
