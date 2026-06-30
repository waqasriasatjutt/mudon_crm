from odoo import api, fields, models


class MudonProject(models.Model):
    """Master list of real-estate projects — referenced from the WON-stage
    `mudon_project_id`. A project usually belongs to a developer
    (`mudon.developer`); the WON form filters projects by the chosen
    developer. Kept as its own model so the client can add / archive
    projects from Configuration without code changes."""

    _name = "mudon.project"
    _description = "Mudon — Project"
    _order = "sequence, name"

    name = fields.Char(required=True)
    developer_id = fields.Many2one(
        "mudon.developer", string="Developer", ondelete="set null",
        help="The developer that owns this project. Leave empty for a "
             "standalone project.",
    )
    pipeline_kind = fields.Selection(
        [("turkey", "Turkey"), ("uae", "UAE Dubai"), ("any", "Any")],
        default="any",
        help="Restricts which pipeline offers this project.",
    )
    notes = fields.Text()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
