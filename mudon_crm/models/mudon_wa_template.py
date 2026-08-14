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
import json
import logging
import urllib.parse
import urllib.request

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

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

    # ─── Live status from Meta ──────────────────────────────────────────
    # A template cannot be sent until Meta approves it, and approval is
    # invisible from inside Odoo. Turning one on while it was still under
    # review is exactly what silently broke the agent alerts, so the
    # status is fetched and shown rather than assumed.
    meta_status = fields.Char(
        string="Approval status", readonly=True, copy=False,
        help="APPROVED means it can be sent. PENDING means Meta is still "
             "reviewing it — sending will fail until it clears.",
    )
    meta_category = fields.Char(
        string="Category", readonly=True, copy=False,
        help="Meta decides this. UTILITY always reaches the recipient. "
             "MARKETING is not delivered to anyone who has opted out of "
             "marketing messages on WhatsApp.",
    )
    meta_body = fields.Text(
        string="Approved wording", readonly=True, copy=False,
        help="Exactly what Meta will send. {{1}}, {{2}} and so on are "
             "replaced with the lead's details.",
    )
    meta_checked_on = fields.Datetime(string="Last checked", readonly=True,
                                      copy=False)
    is_ready = fields.Boolean(
        string="Ready to send", compute="_compute_is_ready", store=True,
    )

    @api.depends("meta_status", "active")
    def _compute_is_ready(self):
        for rec in self:
            rec.is_ready = bool(
                rec.active and (rec.meta_status or "").upper() == "APPROVED")

    @api.model
    def _mudon_meta_credentials(self):
        """Token + business-account id, taken from the configured sender."""
        sender = self.env["mudon.wa.sender"].sudo().search(
            [("waba_id", "!=", False)], order="sequence, id", limit=1)
        ICP = self.env["ir.config_parameter"].sudo()
        token = (sender.access_token
                 or ICP.get_param("mudon_crm.wa_access_token", ""))
        waba = sender.waba_id or ICP.get_param("mudon_crm.wa_waba_id", "")
        version = ICP.get_param("mudon_crm.wa_api_version", "v21.0") or "v21.0"
        return token, waba, version

    @api.model
    def _mudon_fetch_meta_templates(self):
        """{name: {...}} for every template on the business account."""
        token, waba, version = self._mudon_meta_credentials()
        if not (token and waba):
            raise UserError(_(
                "No WhatsApp credentials yet. Add the access token in "
                "Settings > Mudon CRM, and a number with its Business "
                "Account ID under Configuration > WhatsApp Numbers."))
        url = "https://graph.facebook.com/%s/%s/message_templates?%s" % (
            version, waba, urllib.parse.urlencode({
                "access_token": token,
                "fields": "name,status,category,language,components",
                "limit": 200,
            }))
        try:
            with urllib.request.urlopen(url, timeout=25) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            detail = ""
            reader = getattr(exc, "read", None)
            if reader:
                try:
                    detail = reader().decode()[:300]
                except Exception:
                    detail = ""
            raise UserError(
                _("Could not reach Meta: %s %s") % (exc, detail))
        return {t.get("name"): t for t in data.get("data") or []}

    def action_check_meta(self):
        """Refresh approval status for these rows (button on the form)."""
        found = self._mudon_fetch_meta_templates()
        for rec in self:
            meta = found.get(rec.template_name)
            if not meta:
                rec.write({
                    "meta_status": "NOT FOUND",
                    "meta_category": False,
                    "meta_body": False,
                    "meta_checked_on": fields.Datetime.now(),
                })
                continue
            body = ""
            for comp in meta.get("components") or []:
                if (comp.get("type") or "").upper() == "BODY":
                    body = comp.get("text") or ""
            rec.write({
                "meta_status": (meta.get("status") or "").upper(),
                "meta_category": meta.get("category"),
                "meta_body": body,
                "meta_checked_on": fields.Datetime.now(),
            })
        return True

    @api.model
    def action_check_all_meta(self):
        """Refresh every row — the button above the list."""
        self.search([("id", "!=", 0)]).action_check_meta()
        return {"type": "ir.actions.client", "tag": "soft_reload"}

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
