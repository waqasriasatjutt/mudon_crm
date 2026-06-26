from odoo import fields, models


class MudonAdminTask(models.Model):
    """Admin Funnel item spawned on WON write.

    Spec §6: 'Add to Admin Funnel: Need invoice? Yes/No'.

    Kept as its own kanban so the Admin team has a dedicated worklist
    independent of the CRM pipeline. One record per lead per WON
    transition (no idempotence enforced — if a lead is moved out of
    WON and back, a duplicate task lands intentionally so admin can
    see both events).
    """

    _name = "mudon.admin.task"
    _description = "Mudon — Admin Funnel Task"
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
    need_invoice = fields.Boolean(
        help="Carried over from the CRM card at WON time. Drives the "
             "subsequent invoice-issuance workflow.",
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
