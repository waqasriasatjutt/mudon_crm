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
    last_poll_date = fields.Datetime(
        string="Last checked with Meta", readonly=True,
        help="When Odoo last asked Meta whether this form had new leads. "
             "Leads normally arrive within seconds by webhook; this is the "
             "safety net that catches anything the webhook missed.",
    )
    last_poll_found = fields.Integer(
        string="Found on last check", readonly=True,
        help="How many leads that check picked up that had not arrived by "
             "webhook. Anything other than zero here means the webhook is "
             "not delivering reliably.",
    )

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

    @api.depends("form_id")
    def _compute_lead_count(self):
        # Counted on the FORM ID rather than the link back to this record.
        # An event created outside the webhook path (a bulk pull of leads
        # that already existed, say) carries the form id but never got the
        # link set, and this screen then reported 0 leads for a form that
        # had plainly delivered some.
        Event = self.env["mudon.meta.leadgen.event"]
        for rec in self:
            rec.lead_count = Event.search_count([
                ("form_id", "=", rec.form_id), ("lead_id", "!=", False)
            ]) if rec.form_id else 0

    def action_fetch_now(self):
        """Ask Meta for this form's leads right now.

        Same path as the safety-net cron, so nothing can be imported twice.
        """
        found = self.env["mudon.meta.leadgen.event"]._mudon_poll_forms(self)
        self.env["mudon.meta.leadgen.event"]._mudon_cron_process_leadgen()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if found else "info",
                "sticky": False,
                "message": (
                    _("%s lead(s) collected from Meta.") % found if found
                    else _("Nothing new. Meta has no leads on this form that "
                           "the CRM has not already got.")),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_view_leads(self):
        self.ensure_one()
        leads = self.env["mudon.meta.leadgen.event"].search(
            [("form_id", "=", self.form_id)]).mapped("lead_id")
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

    # ─── Safety net: ask Meta what we are missing ──────────────────────
    @api.model
    def _mudon_cron_poll_forms(self, limit_forms=0):
        """Catch leads the webhook never delivered.

        A webhook is fire-and-forget: if Meta's callback is misconfigured, or
        the site is down for the seconds Meta spends retrying, that lead is
        gone and nobody finds out until a client complains. This asks Meta
        directly, so a missed delivery costs minutes rather than the lead.

        It creates the same event rows the webhook creates and leaves the
        processing to the existing cron, so every duplicate guard still
        applies.
        """
        Form = self.env["mudon.meta.lead.form"].sudo()
        forms = Form.search([], limit=limit_forms or None)
        found = self._mudon_poll_forms(forms)
        if found:
            self._mudon_cron_process_leadgen(limit=max(found + 10, 50))
        return found

    @api.model
    def _mudon_poll_forms(self, forms, max_pages=20):
        """Create an event for every Meta lead we have not seen. Returns how many."""
        import urllib.parse
        import urllib.request

        ICP = self.env["ir.config_parameter"].sudo()
        token = ICP.get_param("mudon_crm.meta_page_token", "")
        version = ICP.get_param("mudon_crm.wa_api_version", "v21.0") or "v21.0"
        if not token:
            _logger.warning("mudon_crm: Meta poll skipped, no access token")
            return 0

        total = 0
        for form in forms:
            if not form.form_id:
                continue
            url = "%s/%s/%s/leads?%s" % (
                GRAPH, version, form.form_id,
                urllib.parse.urlencode({
                    "access_token": token, "fields": "id,created_time",
                    "limit": 100,
                }))
            picked = 0
            for _page in range(max_pages):
                try:
                    with urllib.request.urlopen(url, timeout=30) as resp:
                        data = json.loads(resp.read().decode())
                except Exception as exc:
                    _logger.warning(
                        "mudon_crm: Meta poll failed for form %s: %s",
                        form.form_id, exc)
                    break

                rows = data.get("data") or []
                if not rows:
                    break
                ids = [str(r.get("id")) for r in rows if r.get("id")]
                known = set(self.sudo().search(
                    [("leadgen_id", "in", ids)]).mapped("leadgen_id"))
                for lead_id in ids:
                    if lead_id in known:
                        continue
                    if self.mudon_record_event(
                            {"leadgen_id": lead_id, "form_id": form.form_id,
                             "page_id": form.page_id or ""},
                            json.dumps({"source": "poll", "leadgen_id": lead_id})):
                        picked += 1

                # Meta returns newest first. A whole page we already hold means
                # everything older is held too, so there is nothing to gain by
                # walking the rest of the history on every run.
                if not (set(ids) - known):
                    break
                url = ((data.get("paging") or {}).get("next")) or ""
                if not url:
                    break

            form.write({
                "last_poll_date": fields.Datetime.now(),
                "last_poll_found": picked,
            })
            if picked:
                _logger.info(
                    "mudon_crm: Meta poll picked up %s lead(s) the webhook "
                    "missed on form %s", picked, form.form_id)
            total += picked
        return total

    def _process(self):
        self.ensure_one()
        # An event that already produced a lead must never run again. The cron
        # and a manual Retry can pick up the same event within milliseconds of
        # each other, and each would create its own copy of the client.
        if self.lead_id:
            if self.state != "done":
                self.sudo().write({"state": "done", "error": False})
            return
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

        # Second look, as late as possible. The check above reads this
        # event; this one reads the leads themselves, so it also catches an
        # event whose link was lost and a race that got past the first guard.
        Lead = self.env["crm.lead"].sudo()
        lead = Lead.with_context(active_test=False).search(
            [("mudon_meta_leadgen_id", "=", self.leadgen_id)], limit=1)
        if lead:
            self.sudo().write({
                "state": "done", "lead_id": lead.id, "error": False,
                "form_mapping_id": mapping.id,
                "attempts": self.attempts + 1,
            })
            return
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
        """Reopen only the events that never produced a lead.

        Retry used to reset every selected row to pending, so pressing it on a
        row that had already worked created the client a second time.
        """
        again = self.filtered(lambda r: not r.lead_id)
        for rec in again:
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
