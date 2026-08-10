from odoo import fields, models


class MudonLeadStatus(models.Model):
    """Lead status master — managed via Configuration → Lead Statuses.
    Seeds with the four spec values (No Answer 1/2/3, Not Interested);
    admin can add 'Contacted Successfully', 'Hot Lead', etc. without
    touching code.
    """

    _name = "mudon.lead.status"
    _description = "Mudon — Lead Status"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    is_lost_signal = fields.Boolean(
        help="If ticked, leads flipping to this status are flagged as "
             "intent-to-lose (used by automation rules).",
    )

    _code_unique = models.Constraint(
        "unique(code)", "Status code must be unique.")
