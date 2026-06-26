from odoo import fields, models


class MudonAfterSalesTask(models.Model):
    """After-Sales Funnel item spawned on WON write.

    Spec §6: 'Add to After Sales Funnel: Title Deed Required? Y/N ·
    Citizenship Required? Y/N (Turkey-only) · Residence Required? Y/N
    (UAE-only) · Furniture / Other Service Required? Y/N'.

    Each Y answer on the WON form spawns one task of the matching kind,
    so the after-sales kanban is itemised (not a single record with
    multiple booleans). One lead → up to 3 tasks (4 on UAE if you count
    Residence + others).
    """

    _name = "mudon.after.sales.task"
    _description = "Mudon — After-Sales Funnel Task"
    _order = "create_date desc, id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        required=True,
        tracking=True,
        default=lambda s: "/",
    )
    lead_id = fields.Many2one(
        "crm.lead",
        required=True,
        ondelete="cascade",
        tracking=True,
    )
    team_id = fields.Many2one(
        related="lead_id.team_id", store=True, readonly=True,
    )
    kind = fields.Selection(
        [
            ("title_deed", "Title Deed"),
            ("citizenship", "Citizenship"),   # Turkey only
            ("residence", "Residence"),        # UAE only
            ("furniture", "Furniture / Other Service"),
        ],
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("new", "New"),
            ("in_progress", "In Progress"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="new",
        tracking=True,
    )
    assigned_user_id = fields.Many2one(
        "res.users",
        string="Owner",
        tracking=True,
    )
    notes = fields.Text()

    def action_mark_in_progress(self):
        self.write({"state": "in_progress"})

    def action_mark_done(self):
        self.write({"state": "done"})

    def action_cancel(self):
        self.write({"state": "cancelled"})
