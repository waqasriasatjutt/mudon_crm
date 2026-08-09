"""Meta Lead Ads webhook.

Meta POSTs here the moment someone submits an instant form under a Facebook
or Instagram ad. The payload carries IDs only, never the answers:

    {"entry":[{"changes":[{"field":"leadgen","value":{
        "leadgen_id": "...", "form_id": "...", "page_id": "...",
        "created_time": 1234567890}}]}]}

This controller does as little as possible: verify the signature, record
the event, return 200. Meta retries on timeout and will disable a
subscription that keeps failing, so the Graph call that reads the answers
runs a moment later on a cron rather than inside this request.

Callback URL to register in the Meta App:
    https://<your-domain>/mudon/meta/leadgen
Subscribe the page to the `leadgen` field.
"""
import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class MudonMetaLeadgenWebhook(http.Controller):

    @http.route("/mudon/meta/leadgen", type="http", auth="public",
                methods=["GET"], csrf=False)
    def meta_leadgen_verify(self, **kw):
        """One-time verification handshake when the webhook is registered."""
        expected = request.env["ir.config_parameter"].sudo().get_param(
            "mudon_crm.meta_verify_token", "")
        token = kw.get("hub.verify_token", "")
        challenge = kw.get("hub.challenge", "")
        if expected and token == expected and challenge:
            return challenge
        _logger.warning("mudon_crm: Meta leadgen verify rejected")
        return request.make_response("forbidden", status=403)

    @http.route("/mudon/meta/leadgen", type="http", auth="public",
                methods=["POST"], csrf=False)
    def meta_leadgen_inbound(self, **kw):
        """Record each leadgen event and hand it to the cron.

        `type="http"` rather than `"json"` because Meta sends a plain JSON
        document, not a JSON-RPC envelope, which Odoo's JSON dispatcher
        would reject before this method ever ran.
        """
        raw = request.httprequest.get_data(as_text=True) or "{}"

        if not self._signature_ok(raw):
            # A forged POST would inject leads straight into the pipeline,
            # so an unsigned or wrongly-signed body is refused outright.
            _logger.warning("mudon_crm: Meta leadgen bad signature, refused")
            return request.make_response("forbidden", status=403)

        try:
            payload = json.loads(raw)
        except Exception as exc:
            _logger.warning("mudon_crm: Meta leadgen body unparseable: %s", exc)
            payload = {}

        Event = request.env(su=True)["mudon.meta.leadgen.event"]
        recorded = 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                if change.get("field") != "leadgen":
                    continue
                value = change.get("value") or {}
                value.setdefault("page_id", entry.get("id"))
                if Event.mudon_record_event(value, raw):
                    recorded += 1

        if recorded:
            # Turn them into leads straight away rather than waiting for the
            # next cron tick, but never let that failing turn into a non-200,
            # or Meta starts retrying and eventually disables the webhook.
            try:
                Event._mudon_cron_process_leadgen()
            except Exception as exc:
                _logger.warning(
                    "mudon_crm: leadgen processing deferred to cron: %s", exc)

        return request.make_response(
            '{"status":"ok"}',
            headers=[("Content-Type", "application/json")])

    @staticmethod
    def _signature_ok(raw):
        """Validate Meta's X-Hub-Signature-256 against the app secret.

        Returns True when the app secret is not configured, so the webhook
        can be registered and smoke-tested before the secret is in place.
        A warning is logged in that case; set the secret before go-live.
        """
        secret = request.env["ir.config_parameter"].sudo().get_param(
            "mudon_crm.meta_app_secret", "")
        if not secret:
            _logger.warning(
                "mudon_crm: Meta app secret not set, accepting the webhook "
                "unverified. Set it in Settings before going live.")
            return True
        header = request.httprequest.headers.get("X-Hub-Signature-256", "")
        if not header.startswith("sha256="):
            return False
        expected = hmac.new(
            secret.encode(), (raw or "").encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, header.split("=", 1)[1])
