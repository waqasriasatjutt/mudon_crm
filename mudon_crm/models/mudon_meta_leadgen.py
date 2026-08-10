"""Meta Lead Ads to New Lead.

Client requirement: "Complete Connection to Meta Ad to collect the lead
directly to New Lead."

How Meta Lead Ads actually works, which drives the design here:

  1. A person submits an instant form under a Facebook or Instagram ad.
  2. Meta POSTs a webhook to us carrying only IDS, never the answers:
     `{"entry":[{"changes":[{"field":"leadgen","value":{
        "leadgen_id":..., "form_id":..., "page_id":..., "created_time":...}}]}]}`
  3. We then call the Graph API with the leadgen_id and a PAGE token to
     read the answers.

Two consequences shape this module:

  * The webhook must answer FAST. Meta retries on timeout and disables a
    subscription that keeps failing, so the controller only records the
    event and returns 200. A cron does the Graph call and the lead
    creation a moment later. That also gives an audit trail and a retry
    path when Meta or the network hiccups.

  * Meta legitimately re-delivers the same event, so `leadgen_id` carries a
    unique constraint and a processed event is never turned into a second
    lead.

Each Meta form is mapped to a pipeline, because a form belongs to a
campaign that is either Turkey or Dubai, and the CRM has to know which
board the lead belongs on.
"""
import json
import logging

from psycopg2 import IntegrityError, errorcodes

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"

# Field names Meta uses for its built-in questions. Anything not in here is
# a custom question and is preserved verbatim in the lead's notes, so an
# answer is never silently dropped just because we did not anticipate it.
STANDARD_FIELDS = {
    "full_name", "first_name", "last_name", "email", "phone_number",
    "city", "state", "province", "country", "zip", "post_code",
    "company_name", "job_title",
}


class MudonMetaLeadForm(models.Model):
    """A Meta instant form, mapped to the pipeline its leads belong on."""

    _name = "mudon.meta.lead.form"
    _description = "Mudon — Meta Lead Ads form mapping"
    _order = "team_id, name"

    name = fields.Char(
        required=True,
        help="Whatever you call this form. Copy the name from Meta so the "
             "two are easy to line up.",
    )
    form_id = fields.Char(
        string="Meta form ID", required=True, index=True,
        help="From Meta: Business Suite > All tools > Instant Forms, or "
             "Ads Manager > the form's URL. Digits only.",
    )
    page_id = fields.Char(
        string="Facebook page ID",
        help="Optional. Only needed if two pages use forms with the same id.",
    )
    team_id = fields.Many2one(
        "crm.team", string="Pipeline", required=True,
        domain="[('id', 'in', allowed_team_ids)]",
        help="Which board leads from this form land on.",
    )
    allowed_team_ids = fields.Many2many(
        "crm.team", compute="_compute_allowed_team_ids",
    )
    source_id = fields.Many2one(
        "mudon.source", string="Lead source",
        help="Stamped on every lead from this form, so reporting can tell "
             "Meta leads from walk-ins.",
    )
    active = fields.Boolean(default=True)
    lead_count = fields.Integer(compute="_compute_lead_count", string="Leads")

    _form_id_unique = models.Constraint(
        "unique(form_id)", "That Meta form is already mapped.")

    @api.depends("name")
    def _compute_allowed_team_ids(self):
        teams = self.env["crm.team"].search(
            [("id", "in", self._mudon_team_ids())])
        for rec in self:
            rec.allowed_team_ids = teams

    @api.model
    def _mudon_team_ids(self):
        ids = []
        for xmlid in ("mudon_crm.mudon_team_turkey", "mudon_crm.mudon_team_uae"):
            team = self.env.ref(xmlid, raise_if_not_found=False)
            if team:
                ids.append(team.id)
        return ids

    def _compute_lead_count(self):
        Event = self.env["mudon.meta.leadgen.event"]
        for rec in self:
            rec.lead_count = Event.search_count([
                ("form_mapping_id", "=", rec.id), ("lead_id", "!=", False)])

    def action_view_leads(self):
        self.ensure_one()
        leads = self.env["mudon.meta.leadgen.event"].search(
            [("form_mapping_id", "=", self.id)]).mapped("lead_id")
        return {
            "type": "ir.actions.act_window",
            "name": _("Leads from %s", self.name),
            "res_model": "crm.lead",
            "domain": [("id", "in", leads.ids)],
            "views": [[False, "list"], [False, "form"]],
        }


class MudonMetaLeadgenEvent(models.Model):
    """One webhook delivery from Meta, kept as the audit and retry record."""

    _name = "mudon.meta.leadgen.event"
    _description = "Mudon — Meta Lead Ads webhook event"
    _order = "create_date desc"

    leadgen_id = fields.Char(required=True, index=True, readonly=True)
    form_id = fields.Char(index=True, readonly=True)
    page_id = fields.Char(readonly=True)
    form_mapping_id = fields.Many2one("mudon.meta.lead.form", readonly=True)
    lead_id = fields.Many2one("crm.lead", readonly=True, ondelete="set null")
    state = fields.Selection(
        [("pending", "Pending"), ("done", "Lead created"),
         ("skipped", "Skipped"), ("error", "Error")],
        default="pending", index=True, readonly=True,
    )
    attempts = fields.Integer(default=0, readonly=True)
    error = fields.Text(readonly=True)
    raw_payload = fields.Text(readonly=True)
    answers = fields.Text(string="Form answers", readonly=True)

    _leadgen_id_unique = models.Constraint(
        "unique(leadgen_id)", "This Meta lead has already been received.")

    # ─── Ingestion (called by the controller, must be cheap) ────────────
    @api.model
    def mudon_record_event(self, value, raw):
        """Store one leadgen event. Returns True when it is new.

        Meta re-delivers events, so a duplicate leadgen_id is simply
        ignored rather than treated as an error.
        """
        leadgen_id = str(value.get("leadgen_id") or "").strip()
        if not leadgen_id:
            return False
        if self.sudo().search_count([("leadgen_id", "=", leadgen_id)]):
            return False
        try:
            # Meta re-delivers, sometimes fast enough that two requests
            # clear the check above together. The unique index settles
            # which one wins; losing that race is ordinary traffic, not
            # an error, and must not become a non-200 for Meta.
            with self.env.cr.savepoint():
                self.sudo().create({
                    "leadgen_id": leadgen_id,
                    "form_id": str(value.get("form_id") or "").strip(),
                    "page_id": str(value.get("page_id") or "").strip(),
                    "raw_payload": raw[:20000] if raw else False,
                })
        except IntegrityError as exc:
            if exc.pgcode == errorcodes.UNIQUE_VIOLATION:
                return False
            raise
        return True

    # ─── Processing (cron) ──────────────────────────────────────────────
    @api.model
    def _mudon_cron_process_leadgen(self, limit=50):
        events = self.sudo().search(
            [("state", "in", ("pending", "error")), ("attempts", "<", 5)],
            limit=limit, order="create_date asc")
        for event in events:
            try:
                event._process()
            except Exception as exc:          # never let one row stop the run
                _logger.warning(
                    "mudon_crm: leadgen %s failed: %s", event.leadgen_id, exc)
                event.sudo().write({
                    "state": "error", "error": str(exc)[:2000],
                    "attempts": event.attempts + 1,
                })
        return len(events)

    def _process(self):
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()
        token = ICP.get_param("mudon_crm.meta_page_token", "")
        version = ICP.get_param("mudon_crm.wa_api_version", "v21.0") or "v21.0"
        if not token:
            self.sudo().write({
                "state": "error",
                "error": _("No Meta page access token is configured. "
                           "Settings > Mudon CRM > Meta Lead Ads."),
                "attempts": self.attempts + 1,
            })
            return

        mapping = self.env["mudon.meta.lead.form"].sudo().search(
            [("form_id", "=", self.form_id)], limit=1)
        if not mapping:
            # Not an error the client can act on from a log file: say plainly
            # which form id needs mapping.
            self.sudo().write({
                "state": "skipped",
                "error": _("Meta form %s is not mapped to a pipeline yet. "
                           "Add it under Mudon CRM > Configuration > Meta "
                           "Lead Forms, then press Retry.") % self.form_id,
                "attempts": self.attempts + 1,
            })
            return

        data = self._fetch(token, version)
        answers = {}
        for item in data.get("field_data") or []:
            key = (item.get("name") or "").strip().lower()
            values = item.get("values") or []
            if key:
                answers[key] = values[0] if len(values) == 1 else ", ".join(
                    str(v) for v in values)

        lead = self.env["crm.lead"]._mudon_create_from_meta(
            answers, mapping, self.leadgen_id)
        self.sudo().write({
            "state": "done", "lead_id": lead.id, "error": False,
            "form_mapping_id": mapping.id,
            "answers": json.dumps(answers, ensure_ascii=False, indent=2),
            "attempts": self.attempts + 1,
        })

    def _fetch(self, token, version):
        """Read the submitted answers from the Graph API."""
        self.ensure_one()
        import urllib.parse
        import urllib.request
        url = "%s/%s/%s?%s" % (
            GRAPH, version, self.leadgen_id,
            urllib.parse.urlencode({
                "access_token": token,
                "fields": "id,created_time,form_id,field_data",
            }))
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            detail = ""
            body = getattr(exc, "read", None)
            if body:
                try:
                    detail = body().decode()[:400]
                except Exception:
                    detail = ""
            raise UserWarning(
                _("Could not read the lead from Meta: %s %s") % (exc, detail))

    def action_retry(self):
        for rec in self:
            rec.sudo().write({"state": "pending", "attempts": 0})
        return self._mudon_cron_process_leadgen()

    def action_open_lead(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "crm.lead",
            "res_id": self.lead_id.id,
            "view_mode": "form",
        }
