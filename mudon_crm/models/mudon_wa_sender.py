"""One WhatsApp number the CRM sends from.

Client requirement: "1 number for Dubai Funnel, 1 number for Turkey
Funnel". The settings panel only ever modelled a single number, so this
adds a small list instead of another six settings fields — the client
can add a third number later without a code change.

How this maps onto Meta, which is what makes the fields make sense:

    App
     └── WhatsApp Business Account (WABA)      <- one `waba_id`
          ├── +90 ... Turkey                    <- its own `phone_number_id`
          └── +971 ... Dubai                    <- its own `phone_number_id`

The access token belongs to the WABA, not to a number. So when both
numbers live under the same WABA — the normal setup — ONE token covers
both and only the phone-number ids differ. `access_token` here is for
the case where a number sits under a different business account, and is
left blank otherwise.
"""
from odoo import api, fields, models

WA_PIPELINE_KIND = [
    ("turkey", "Turkey"),
    ("uae", "UAE Dubai"),
    ("any", "Any pipeline"),
]

WA_SENDER_ROLE = [
    ("agent", "Agent notifications only"),
    ("company", "Company sends only (client survey)"),
    ("both", "Both"),
]


class MudonWaSender(models.Model):
    _name = "mudon.wa.sender"
    _description = "Mudon — WhatsApp sending number"
    _order = "sequence, id"

    name = fields.Char(
        required=True,
        help="Whatever you call it internally, e.g. 'Mudon Dubai'.",
    )
    display_number = fields.Char(
        string="Number",
        help="The number in readable form, e.g. +971 50 289 0693. Shown in "
             "the chatter so staff can tell which line a message went out "
             "on. Not used to send.",
    )
    pipeline_kind = fields.Selection(
        WA_PIPELINE_KIND, string="Pipeline", required=True, default="any",
        help="Which board's leads send from this number. 'Any pipeline' is "
             "the fallback used when a pipeline has no number of its own.",
    )
    role = fields.Selection(
        WA_SENDER_ROLE, required=True, default="both",
        help="'Company sends' is the separate line the 3rd-offer client "
             "survey goes out on. Leave on Both unless you run a separate "
             "company line.",
    )
    phone_number_id = fields.Char(
        string="Phone number ID", required=True,
        help="From Meta: WhatsApp > API Setup, the 'Phone number ID' under "
             "the number itself. Digits only. This is NOT the phone number.",
    )
    waba_id = fields.Char(
        string="WhatsApp Business Account ID",
        help="From the same screen. Recorded for reference and support; "
             "sending does not need it.",
    )
    access_token = fields.Char(
        help="Leave blank when this number is under the same business "
             "account as the others — the token in Settings covers it. "
             "Only fill this in if this number belongs to a different "
             "business account with its own token.",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    _phone_number_id_unique = models.Constraint(
        "unique(phone_number_id)",
        "That phone-number ID is already registered on another sender.")

    @api.model
    def _mudon_resolve(self, pipeline_kind, from_company=False):
        """Pick the number to send from.

        Role is matched before pipeline, deliberately. The 3rd-offer
        client survey is specified to go out from the company line, so a
        number dedicated to that role must beat a general-purpose
        pipeline number — otherwise setting up a company line and a
        Dubai line would silently send the survey from Dubai.

        Order tried:
          1. this pipeline, dedicated to this role
          2. any pipeline, dedicated to this role
          3. this pipeline, marked Both
          4. any pipeline, marked Both

        Returns an empty recordset when nothing matches; the caller then
        falls back to the single-number settings.
        """
        role = "company" if from_company else "agent"
        this = pipeline_kind or "any"
        for role_domain in ([("role", "=", role)], [("role", "=", "both")]):
            for pipeline in (this, "any"):
                found = self.sudo().search(
                    role_domain + [("pipeline_kind", "=", pipeline)],
                    order="sequence, id", limit=1)
                if found:
                    return found
        return self.browse()
