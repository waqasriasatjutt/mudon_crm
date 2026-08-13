"""Maps each automated message to an approved WhatsApp template.

Why this exists: WhatsApp only accepts free-form text inside the
24-hour window that opens when the CUSTOMER messages the business.
Every message Mudon sends is business-initiated — a lead who just filled
in an ad form has never messaged anyone — so free text is accepted by the
API, given a message id, and then silently dropped. Nothing arrives and
nothing is reported, because the failure comes back on the delivery
webhook rather than on the send call.

Templates are the supported path for business-initiated messages. They
are approved once by Meta and then deliver to anyone.

The registry is a model rather than hard-coded names because the client
approves templates in his own Meta account, under whatever names Meta
lets him have, and may re-approve them under new names later.
"""
from odoo import api, fields, models

# Every automated message, and how many {{n}} parameters its template
# takes. `agent_alert` covers all agent/manager notifications: they share
# one body shape and differ only by the headline, which is parameter 1.
MUDON_WA_MESSAGE_KEYS = [
    ("new_lead_greeting", "New lead — greeting to the client"),
    ("agent_alert", "Agent / manager notification (all kinds)"),
    ("client_survey", "3rd-offer survey to the client"),
]

MUDON_WA_PARAM_COUNT = {
    "new_lead_greeting": 0,
    "agent_alert": 4,        # headline, client name, WhatsApp link, card link
    "client_survey": 1,      # client name
}


class MudonWaTemplate(models.Model):
    _name = "mudon.wa.template"
    _description = "Mudon — WhatsApp message template"
    _order = "message_key"

    message_key = fields.Selection(
        MUDON_WA_MESSAGE_KEYS, required=True,
        help="Which automated message this template is used for.",
    )
    template_name = fields.Char(
        required=True,
        help="Exactly as approved in Meta, e.g. mudon_new_lead_greeting. "
             "Lower case, underscores, no spaces.",
    )
    lang_code = fields.Char(
        string="Language", required=True, default="en",
        help="The template's language code in Meta — 'en' or 'en_US'. "
             "These are not interchangeable; copy what Meta shows.",
    )
    active = fields.Boolean(default=True)
    note = fields.Char(
        string="Parameters",
        compute="_compute_note",
        help="What Meta will substitute into {{1}}, {{2}} and so on.",
    )

    _message_key_unique = models.Constraint(
        "unique(message_key)",
        "There is already a template mapped to that message.")

    @api.depends("message_key")
    def _compute_note(self):
        described = {
            "new_lead_greeting": "no parameters",
            "agent_alert": "{{1}} headline · {{2}} client name · "
                           "{{3}} WhatsApp link · {{4}} card link",
            "client_survey": "{{1}} client name",
        }
        for rec in self:
            rec.note = described.get(rec.message_key, "")

    @api.model
    def _mudon_for(self, key):
        """The approved template for `key`, or an empty recordset.

        Empty means "not configured yet", and the caller falls back to
        plain text — which still works inside an open 24-hour window, so
        a half-configured system degrades rather than breaks.
        """
        if not key:
            return self.browse()
        return self.sudo().search([("message_key", "=", key)], limit=1)
