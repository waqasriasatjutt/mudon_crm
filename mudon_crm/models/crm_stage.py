from odoo import fields, models


MUDON_STAGE_KIND_SELECTION = [
    ("new_lead", "New Lead"),
    ("qualified", "Qualified"),
    ("offer_sent", "Offer Sent"),
    ("meeting", "Meeting"),
    ("eoi", "EOI / Booking"),
    ("won", "WON (SPA Signed)"),
    ("lost", "Lost"),
]


class CrmStage(models.Model):
    """Tag every stage with a semantic kind so transitions, SLA crons
    and automation logic across the module reference `stage_id.kind ==
    'offer_sent'` instead of `env.ref('mudon_crm.mudon_stage_turkey_
    offer_sent')`.

    Adding a new Mudon-style pipeline (e.g. KSA) then only means
    seeding stages with the right `mudon_stage_kind` — no code
    changes anywhere else in the module.
    """

    _inherit = "crm.stage"

    mudon_stage_kind = fields.Selection(
        MUDON_STAGE_KIND_SELECTION,
        string="Mudon Stage Kind",
        help="Semantic role of this stage within a Mudon pipeline. "
             "Drives stage-transition writes, SLA crons, and the "
             "kanban color-coding logic. Leave empty for non-Mudon "
             "stages.",
        index=True,
    )
