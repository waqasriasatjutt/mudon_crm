from odoo import api, fields, models


class MudonLostReason(models.Model):
    """2-level taxonomy: 5 parent categories × 2-3 subreasons each.
    Lead picks a LEAF (subreason). Category is derivable via
    `parent_id`. Admin can add new subreasons or whole new categories
    without touching code.

    Seed data (loaded on install):
      Irrelevant Lead       → Asking for services we don't offer
                            → Wrong country
                            → Fake / spam
      Not Reachable         → No answer after 3 attempts
                            → Wrong number
      Low Intent            → Just exploring
                            → No urgency
      Financial Mismatch    → Budget too low
                            → Payment plan unsuitable
      Product Mismatch      → Didn't like options
                            → Location not suitable
    """

    _name = "mudon.lost.reason"
    _description = "Mudon — Lost Reason"
    _parent_store = True
    _parent_name = "parent_id"
    _rec_name = "complete_name"
    _order = "complete_name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    parent_id = fields.Many2one(
        "mudon.lost.reason",
        ondelete="cascade",
        index=True,
        help="Leave empty on category rows. Set to the category on "
             "subreasons.",
    )
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("mudon.lost.reason", "parent_id")
    is_category = fields.Boolean(
        compute="_compute_is_category", store=True,
        help="True for top-level category rows; False for selectable "
             "subreasons.",
    )
    complete_name = fields.Char(
        compute="_compute_complete_name", store=True,
        recursive=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _code_unique = models.Constraint(
        "unique(code)", "Lost-reason code must be unique.")

    @api.depends("parent_id")
    def _compute_is_category(self):
        for rec in self:
            rec.is_category = not rec.parent_id

    @api.depends("name", "parent_id.complete_name")
    def _compute_complete_name(self):
        for rec in self:
            if rec.parent_id:
                rec.complete_name = "%s — %s" % (
                    rec.parent_id.complete_name or rec.parent_id.name,
                    rec.name,
                )
            else:
                rec.complete_name = rec.name
