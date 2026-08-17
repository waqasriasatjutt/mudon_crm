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

    # ─── Authoring ──────────────────────────────────────────────────────
    # The wording is written here and submitted to Meta from here. Making
    # the client author it in WhatsApp Manager and then copy the name back
    # was the wrong way round: two screens, a name to mistype, and no way
    # to tell whether what was approved is what the CRM will send.
    category = fields.Selection(
        [("UTILITY", "Utility — a reply or an update (cheaper, always delivered)"),
         ("MARKETING", "Marketing — promotional (dearer, blocked for opted-out people)")],
        default="UTILITY", required=True,
        help="Meta has the final say and may reclassify it. Wording that "
             "reads as a reply to the customer usually stays Utility.",
    )
    body_text = fields.Text(
        string="Message wording",
        help="Write the message. Use {{1}}, {{2}} where the lead's details "
             "should appear — the list above the box tells you which is which.",
    )
    submitted = fields.Boolean(readonly=True, copy=False,
                               help="Sent to Meta for approval at least once.")

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

    # Example values Meta requires alongside a template that has {{n}}
    # placeholders — it reviews the wording with these filled in.
    MUDON_WA_EXAMPLES = {
        "new_lead_greeting": [],
        "agent_alert": ["You have new client!", "Ahmed Al Mansoori",
                        "https://wa.me/971502890693",
                        "https://example.com/odoo/lead/42"],
        "client_survey": ["Ahmed Al Mansoori"],
    }

    @api.onchange("message_key")
    def _onchange_message_key_defaults(self):
        """Fill the name and a starting wording so the page is never blank."""
        starters = {
            # Bilingual, because the client specified both languages. The
            # wording that reaches a customer is whatever Meta approved,
            # not the Python body used for the chatter copy — so if the
            # Arabic is missing HERE it never reaches anybody.
            "new_lead_greeting": (
                "Thank you for contacting Mudon.\n"
                "One of our property advisors will contact you shortly.\n\n"
                "شكراً لتوا"
                "صلكم مع مدن\n"
                "سيقوم مستش"
                "ار عقاري "
                "بالتواصل "
                "معكم قريباً."),
            "agent_alert": (
                "Mudon CRM notification: {{1}}\n\n"
                "Client name: {{2}}\n"
                "Open the WhatsApp conversation with this client here: {{3}}\n"
                "Open the full client record in the CRM here: {{4}}\n\n"
                "This message was sent automatically by the Mudon CRM system "
                "to the sales colleague responsible for this client."),
            "client_survey": (
                "Hello {{1}}, we have sent you a few property options from "
                "Mudon. Could you let us know whether any of them suit you, "
                "and whether you would like the same advisor to continue "
                "helping you? Your answer helps us send you better options."),
        }
        for rec in self:
            if rec.message_key and not rec.template_name:
                rec.template_name = "mudon_%s" % rec.message_key
            if rec.message_key and not rec.body_text:
                rec.body_text = starters.get(rec.message_key, "")

    def _mudon_placeholder_count(self):
        """How many distinct {{n}} the wording uses."""
        self.ensure_one()
        import re
        return len(set(re.findall(r"\{\{(\d+)\}\}", self.body_text or "")))

    def action_submit_to_meta(self):
        """Create this template on the business account and await review."""
        self.ensure_one()
        if not self.body_text:
            raise UserError(_("Write the message wording first."))
        needed = MUDON_WA_PARAM_COUNT.get(self.message_key, 0)
        found = self._mudon_placeholder_count()
        if found != needed:
            raise UserError(_(
                "This message fills in %(needed)s detail(s), but the wording "
                "uses %(found)s. Use exactly %(needed)s placeholders: %(legend)s",
                needed=needed, found=found, legend=self.note or "none"))
        name = (self.template_name or "").strip().lower()
        if not name.replace("_", "").isalnum():
            raise UserError(_(
                "The name may only contain lower-case letters, numbers and "
                "underscores. No spaces."))
        token, waba, version = self._mudon_meta_credentials()
        if not (token and waba):
            raise UserError(_(
                "No WhatsApp credentials yet. Add the access token in "
                "Settings > Mudon CRM and a number with its Business "
                "Account ID under Configuration > WhatsApp Numbers."))
        component = {"type": "BODY", "text": self.body_text}
        example = self.MUDON_WA_EXAMPLES.get(self.message_key) or []
        if needed and example:
            component["example"] = {"body_text": [example[:needed]]}
        payload = {
            "name": name,
            "language": self.lang_code or "en",
            "category": self.category or "UTILITY",
            "components": [component],
        }
        req = urllib.request.Request(
            "https://graph.facebook.com/%s/%s/message_templates" % (
                version, waba),
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer %s" % token,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                json.loads(resp.read().decode())
        except Exception as exc:
            detail = ""
            reader = getattr(exc, "read", None)
            if reader:
                try:
                    detail = json.loads(reader().decode()).get(
                        "error", {}).get("error_user_msg", "") or ""
                except Exception:
                    detail = ""
            raise UserError(_(
                "Meta would not accept this template.\n\n%s\n\nA common cause "
                "is too many placeholders for the amount of wording — add more "
                "plain words, or a template with this name already exists, in "
                "which case press Check status instead.") % (detail or exc))
        self.write({"template_name": name, "submitted": True})
        self.action_check_meta()
        return {"type": "ir.actions.client", "tag": "soft_reload"}

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
