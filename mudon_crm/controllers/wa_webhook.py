"""WhatsApp Cloud API inbound webhook.

Receives messages from Meta's WhatsApp Cloud API and:

  1. Verifies the GET verification challenge during webhook registration.
  2. On POST: walks `entry[*].changes[*].value.messages[*]` and dispatches
     each message to `_mudon_handle_inbound_message`.

What the inbound handler does today:

  - Match the sender phone to a `res.users.phone` to identify the agent,
    fall back to matching against `crm.lead.phone` to identify the client.
  - If the message body matches a known command keyword (case-insensitive,
    trimmed), perform the bound action:

        "offer sent"  → set `mudon_tick_offer_sent = True` on the matched
                        lead OR bump `mudon_offer_counter` if already on
                        the Offer Sent stage.

  - Otherwise: log the message body to the lead's chatter as
    `'WA inbound (from client/agent): ...'`. Survey responses land here
    today; a follow-up milestone will parse them into the dedicated
    `mudon_survey_*` Selection fields.

The verification challenge token is read from
`ir.config_parameter` key `mudon_crm.wa_verify_token`. Set this to the
same value you configured on the Meta App webhook page before the
first call.
"""
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


COMMAND_OFFER_SENT_KEYWORDS = (
    "offer sent", "offersent", "send offer", "offer was sent",
)


class MudonWhatsAppWebhook(http.Controller):

    @http.route(
        "/mudon/wa/webhook", type="http", auth="public",
        methods=["GET"], csrf=False,
    )
    def wa_webhook_verify(self, **kw):
        """Meta sends a one-time verification GET with `hub.mode`,
        `hub.verify_token`, `hub.challenge`. We echo back `hub.challenge`
        only when the token matches what's stored in config."""
        expected = request.env["ir.config_parameter"].sudo().get_param(
            "mudon_crm.wa_verify_token", "",
        )
        token = kw.get("hub.verify_token", "")
        challenge = kw.get("hub.challenge", "")
        if expected and token == expected and challenge:
            return challenge
        return request.make_response(
            "forbidden", status=403,
        )

    @http.route(
        "/mudon/wa/webhook", type="http", auth="public",
        methods=["POST"], csrf=False,
    )
    def wa_webhook_inbound(self, **kw):
        """Meta posts a flat JSON envelope per inbound message.

        type="http" (not "json") is required because Meta does NOT send
        a JSON-RPC envelope — its body is a plain `{"entry": [...]}`
        document, so Odoo's JSON-RPC dispatcher would reject it before
        the controller runs. We parse the body manually and return a
        non-JSON-RPC 200 so Meta keeps the subscription live.

        Sample inbound shape (simplified):
            {
              "entry": [{
                "changes": [{
                  "value": {
                    "messages": [{
                      "from": "966500000001",
                      "text": {"body": "Offer Sent"}
                    }]
                  }
                }]
              }]
            }
        """
        try:
            raw = request.httprequest.get_data(as_text=True) or "{}"
            payload = json.loads(raw)
        except Exception as exc:
            _logger.warning(
                "mudon_crm: WA webhook body not parseable: %s", exc,
            )
            payload = {}

        env = request.env(su=True)
        Lead = env["crm.lead"]
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                for msg in value.get("messages") or []:
                    sender = msg.get("from") or ""
                    body = ((msg.get("text") or {}).get("body") or "").strip()
                    if not sender:
                        continue
                    Lead._mudon_handle_inbound_wa_message(
                        sender_phone=sender, body=body,
                    )
        return request.make_response(
            '{"status":"ok"}',
            headers=[("Content-Type", "application/json")],
        )
