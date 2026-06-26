import logging
import re
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


PIPELINE_KIND_SELECTION = [
    ("turkey", "Turkey CBI / Investment"),
    ("uae", "UAE Dubai Golden Visa / Investment"),
]


SERVICE_SELECTION = [
    ("citizenship", "Citizenship"),       # Turkey
    ("goldenvisa", "Golden Visa"),         # UAE
    ("investment", "Investment"),          # both
]


CITY_SELECTION = [
    ("istanbul", "Istanbul"),              # Turkey
    ("trabzon", "Trabzon"),                # Turkey
    ("other_tr", "Other (Turkey)"),        # Turkey, free-text in mudon_city_other
    ("dubai", "Dubai"),                    # UAE
    ("abudhabi", "Abu Dhabi"),             # UAE
    ("sharjah", "Sharjah"),                # UAE
    ("rak", "Ras Al Khaimah"),             # UAE
]


PRIORITY_SELECTION = [
    ("urgent", "Urgent"),
    ("normal", "Normal"),
]


STATUS_SELECTION = [
    ("no_answer_1", "No Answer 1"),
    ("no_answer_2", "No Answer 2"),
    ("no_answer_3", "No Answer 3"),
    ("not_interested", "Not Interested"),
]


PURPOSE_SELECTION = [
    ("citizenship", "Citizenship"),         # Turkey only
    ("goldenvisa", "Golden Visa"),          # UAE only
    ("residency_permit", "Residency Permit"),
    ("end_user", "End User"),
    ("investment", "Investment"),
    ("other", "Other"),
]


PROPERTY_TYPE_SELECTION = [
    ("apartment", "Apartment"),
    ("villa", "Villa"),
    ("townhouse", "Townhouse"),
    ("penthouse", "Penthouse"),
    ("duplex", "Duplex"),
    ("mansion", "Mansion"),
    ("hotel_apartment", "Hotel Apartment"),
    ("office", "Office"),
    ("retail_shop", "Retail Shop"),
    ("warehouse", "Warehouse"),
    ("land", "Land"),
    ("farm", "Farm"),
]


SOURCE_SELECTION = [
    # System sources (auto-tagged when leads come in via integrations)
    ("meta", "META"),
    ("google_ads", "Google Ads"),
    ("website", "Website"),
    ("whatsapp_direct", "WhatsApp Direct"),
    ("property_finder", "Property Finder"),
    ("bayut", "Bayut"),
    # Manual sources (agent picks at lead-creation)
    ("referral", "Referral"),
    ("existing_client", "Existing Client"),
    ("agency_partner", "Agency Partner"),
    ("instagram", "Instagram"),
    ("tiktok", "TikTok"),
    ("linkedin", "LinkedIn"),
    ("exhibition_event", "Exhibition / Event"),
    ("cold_call", "Cold Call"),
    ("manual_entry", "Manual Entry"),
    ("other", "Other"),
]


class CrmLead(models.Model):
    _inherit = "crm.lead"
    _order = "create_date desc, id desc"  # "Most Recent on top" per spec

    # ─── Pipeline marker ────────────────────────────────────────────
    # Computed from `team_id` via team xmlid lookup. Lets us toggle
    # field visibility on the form (Turkey-only / UAE-only) without
    # threading a hard-coded team id through the view definition.
    mudon_pipeline_kind = fields.Selection(
        PIPELINE_KIND_SELECTION,
        string="Mudon Pipeline",
        compute="_compute_mudon_pipeline_kind",
        store=True,
        index=True,
    )

    # ─── Stage-1 fields (shared) ────────────────────────────────────
    mudon_service = fields.Selection(
        SERVICE_SELECTION,
        string="MService",
    )
    mudon_city = fields.Selection(
        CITY_SELECTION,
        string="MCity",
    )
    mudon_city_other = fields.Char(
        string="Other City",
        help="Free-text city when MCity = 'Other (Turkey)'.",
    )
    mudon_priority = fields.Selection(
        PRIORITY_SELECTION,
        string="MPriority",
        default="normal",
    )
    mudon_budget = fields.Monetary(
        string="MBudget",
        currency_field="mudon_budget_currency_id",
    )
    mudon_budget_currency_id = fields.Many2one(
        "res.currency",
        string="Budget Currency",
        default=lambda s: s.env.ref("base.USD").id,
    )
    mudon_status = fields.Selection(
        STATUS_SELECTION,
        string="Status",
    )
    mudon_nationality_id = fields.Many2one(
        "res.country",
        string="Nationality",
    )
    mudon_living_in_id = fields.Many2one(
        "res.country",
        string="Living In",
    )
    mudon_in_country = fields.Boolean(
        string="In Country Now",
        help="Turkey pipeline: 'In Turkey Now'. "
             "UAE pipeline: 'In UAE Now'.",
    )
    mudon_purpose = fields.Selection(
        PURPOSE_SELECTION,
        string="Purpose of the Property",
    )
    mudon_property_type = fields.Selection(
        PROPERTY_TYPE_SELECTION,
        string="Property Type",
    )
    mudon_beds = fields.Integer(string="No. of Beds")
    mudon_other_specs = fields.Text(string="Other Specifications")
    mudon_visit_date = fields.Date(string="Expected Visit Date")
    mudon_notes = fields.Text(string="Notes")
    mudon_source = fields.Selection(
        SOURCE_SELECTION,
        string="Source",
    )

    # ─── Turkey-only fields ─────────────────────────────────────────
    mudon_cbi_files = fields.Integer(string="No. of CBI Files")

    # ─── SLA tracking (set when reminder/escalation fires) ──────────
    mudon_sla_30min_fired = fields.Boolean(
        string="SLA 30-min fired",
        default=False,
        copy=False,
    )
    mudon_sla_1hour_fired = fields.Boolean(
        string="SLA 1-hour fired",
        default=False,
        copy=False,
    )
    mudon_first_contact_logged = fields.Boolean(
        string="First Contact Logged",
        default=False,
        copy=False,
        help="Set True the first time the agent posts a chatter "
             "message after lead creation. Stops further SLA escalations.",
    )

    # ─── Computes ───────────────────────────────────────────────────
    @api.depends("team_id")
    def _compute_mudon_pipeline_kind(self):
        turkey_team = self.env.ref(
            "mudon_crm.mudon_team_turkey", raise_if_not_found=False,
        )
        uae_team = self.env.ref(
            "mudon_crm.mudon_team_uae", raise_if_not_found=False,
        )
        for rec in self:
            if turkey_team and rec.team_id.id == turkey_team.id:
                rec.mudon_pipeline_kind = "turkey"
            elif uae_team and rec.team_id.id == uae_team.id:
                rec.mudon_pipeline_kind = "uae"
            else:
                rec.mudon_pipeline_kind = False

    # ─── Create override: auto-assign + client greeting + agent notif ──
    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        for lead in leads:
            try:
                lead._mudon_auto_assign_agent()
                lead._mudon_send_client_greeting()
                lead._mudon_notify_assigned_agent("new_lead")
            except Exception as exc:
                _logger.warning(
                    "mudon_crm: post-create hook failed for lead %s: %s",
                    lead.id, exc,
                )
        return leads

    # ─── Auto-assignment ────────────────────────────────────────────
    def _mudon_auto_assign_agent(self):
        """Pick a salesperson for this lead using the country-code map,
        falling back to round-robin across team members.

        Only fires for leads on a Mudon team that don't already have a
        user_id set (e.g. by a public-website webhook that didn't pick
        an owner).
        """
        self.ensure_one()
        if self.user_id or not self.mudon_pipeline_kind:
            return
        agent = self._mudon_pick_by_country_code() \
            or self._mudon_pick_round_robin()
        if agent:
            self.write({"user_id": agent.id})

    def _mudon_pick_by_country_code(self):
        """Look up a country-code mapping for this lead's phone.
        Matches team-specific mappings first, then global ones."""
        self.ensure_one()
        prefix = self._mudon_phone_prefix()
        if not prefix:
            return self.env["res.users"]
        Mapping = self.env["mudon.country.agent.mapping"].sudo()
        # team-specific match wins
        m = Mapping.search([
            ("country_code", "=", prefix),
            ("team_id", "=", self.team_id.id),
        ], limit=1)
        if not m:
            # global (team_id = false) fallback
            m = Mapping.search([
                ("country_code", "=", prefix),
                ("team_id", "=", False),
            ], limit=1)
        return m.agent_user_id

    def _mudon_pick_round_robin(self):
        """Round-robin across team members ordered by `id`.

        Picks the member who comes AFTER the most-recently-assigned
        member in the ordered list. We sort the "last assignment"
        lookup by `id desc` (not create_date) so that when several
        leads land in the same second — e.g. a webhook batch or a
        multi-row create — each sibling sees the previous sibling
        as 'last' and gets the next agent in rotation, instead of
        every sibling collapsing onto the same agent.
        """
        self.ensure_one()
        members = self.team_id.member_ids.sorted("id")
        if not members:
            return self.env["res.users"]
        last = self.env["crm.lead"].sudo().search([
            ("team_id", "=", self.team_id.id),
            ("user_id", "in", members.ids),
            ("id", "!=", self.id),
        ], order="id desc", limit=1)
        if not last or last.user_id not in members:
            return members[0]
        idx = list(members).index(last.user_id)
        return members[(idx + 1) % len(members)]

    @staticmethod
    def _mudon_phone_normalize(phone):
        """Strip everything except digits + leading +.

        Handles three messy inputs from public web-forms:
          - extra punctuation ("+966 50 123-4567")
          - `00` IDD prefix in place of `+`
          - one or more embedded `+` ("+966+501234567")

        National-format numbers (no `+`, no `00`) are returned
        unchanged. The caller treats lack of leading `+` as "no
        prefix available" and falls through to round-robin.
        """
        if not phone:
            return ""
        cleaned = re.sub(r"[^\d+]", "", phone)
        if cleaned.startswith("00"):
            cleaned = "+" + cleaned[2:]
        if "+" in cleaned:
            cleaned = "+" + cleaned.replace("+", "")
        return cleaned

    def _mudon_phone_prefix(self):
        """Best-effort country-code extraction from phone.

        Tries 3-digit then 2-digit then 1-digit prefix and returns
        the first one matching a country-code mapping. This avoids
        false matches when (e.g.) +1 (US) overlaps the start of +1xxx
        North-American numbers that don't have an explicit mapping.
        """
        self.ensure_one()
        normalized = self._mudon_phone_normalize(self.phone)
        if not normalized.startswith("+"):
            return ""
        digits = normalized[1:]
        Mapping = self.env["mudon.country.agent.mapping"].sudo()
        for n in (3, 2, 1):
            if len(digits) >= n:
                prefix = digits[:n]
                if Mapping.search_count([("country_code", "=", prefix)]):
                    return prefix
        return ""

    # ─── WhatsApp messaging (provider-agnostic stub) ────────────────
    def _mudon_send_whatsapp(self, phone, body):
        """Send a WhatsApp message. Provider is selected via the
        `mudon_crm.wa_provider` config parameter:

          - "stub"   (default) — log to chatter only
          - "meta"   — TODO: POST to Meta WhatsApp Cloud API
          - "twilio" — TODO: POST to Twilio WhatsApp API

        Concrete senders will be added once the client provides
        credentials (proposal §6 — Not Included).

        Passes `mudon_skip_first_contact=True` in context when posting
        to chatter so the message_post override doesn't false-flag the
        system-generated stub note as 'agent has made first contact'
        and thereby skip the SLA escalations.
        """
        self.ensure_one()
        provider = self.env["ir.config_parameter"].sudo().get_param(
            "mudon_crm.wa_provider", "stub",
        )
        normalized = self._mudon_phone_normalize(phone)
        if provider == "stub":
            stub_body = Markup(
                "<p><b>[WA STUB → %s]</b></p>%s"
            ) % (escape(normalized or "(no number)"), body)
            self.with_context(mudon_skip_first_contact=True).message_post(
                body=stub_body,
                subject=_("WhatsApp (stub send)"),
            )
            return True
        # Real-provider branches land here in M1.5 once creds arrive.
        _logger.warning(
            "mudon_crm: WA provider '%s' is configured but no concrete "
            "sender exists yet — message NOT sent for lead %s.",
            provider, self.id,
        )
        return False

    def _mudon_send_client_greeting(self):
        """Bilingual greeting to the prospect's WhatsApp."""
        self.ensure_one()
        if not self.phone:
            return
        body = Markup(
            "<p>Thank you for contacting Mudon<br/>"
            "One of our property advisors will contact you shortly.</p>"
            "<p dir=\"rtl\" lang=\"ar\">شكراً لتواصلكم مع مدن<br/>"
            "سيقوم مستشار عقاري بالتواصل معكم قريباً.</p>"
        )
        self._mudon_send_whatsapp(self.phone, body)

    def _mudon_notify_assigned_agent(self, kind):
        """Send a WA message to the assigned agent.

        kind:
          - "new_lead"      — initial notification (immediate on assign)
          - "sla_30min"     — 30-minute reminder
          - "sla_1hour"     — 1-hour escalation

        Dynamic strings (`contact_name`, links) are escaped via
        markupsafe before being merged into the body. With the stub
        provider this only matters because Odoo's chatter renders the
        message_post body as HTML, but the same render path will copy
        verbatim into the Meta/Twilio sender — so harden once.
        """
        self.ensure_one()
        if not self.user_id or not self.user_id.phone:
            return
        wa_link = "https://wa.me/%s" % (
            self._mudon_phone_normalize(self.phone).lstrip("+") or "",
        )
        base_url = self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "",
        )
        card_link = "%s/odoo/action-crm.crm_lead_action_pipeline/%s" % (
            base_url, self.id,
        )
        headline_map = {
            "new_lead": _("You have new client!"),
            "sla_30min": _("You didn't contact the client!"),
            "sla_1hour": _("didn't contact client for 1 hrs."),
        }
        headline = headline_map.get(kind, _("Lead update"))
        body = Markup(
            "<p><b>%s</b></p>"
            "<p>Name: %s<br/>"
            "Link to WhatsApp Chat: <a href=\"%s\">%s</a><br/>"
            "Link to Client Card: <a href=\"%s\">%s</a></p>"
        ) % (
            escape(headline),
            escape(self.contact_name or self.name or "(no name)"),
            escape(wa_link), escape(wa_link),
            escape(card_link), escape(card_link),
        )
        self._mudon_send_whatsapp(self.user_id.phone, body)
        # Manager escalation also pings the team leader on the 1-hour mark.
        if kind == "sla_1hour" and self.team_id.user_id \
                and self.team_id.user_id.phone:
            self._mudon_send_whatsapp(self.team_id.user_id.phone, body)

    # ─── SLA cron handlers ──────────────────────────────────────────
    @api.model
    def _mudon_cron_sla_30min(self):
        return self._mudon_sla_sweep(minutes=30, kind="sla_30min")

    @api.model
    def _mudon_cron_sla_1hour(self):
        return self._mudon_sla_sweep(minutes=60, kind="sla_1hour")

    @api.model
    def _mudon_sla_sweep(self, minutes, kind):
        threshold = fields.Datetime.now() - timedelta(minutes=minutes)
        domain = [
            ("create_date", "<=", threshold),
            ("mudon_first_contact_logged", "=", False),
            ("mudon_pipeline_kind", "in", ("turkey", "uae")),
        ]
        domain.append(
            ("mudon_sla_30min_fired", "=", False)
            if kind == "sla_30min"
            else ("mudon_sla_1hour_fired", "=", False)
        )
        leads = self.search(domain)
        for lead in leads:
            lead._mudon_notify_assigned_agent(kind)
            if kind == "sla_30min":
                lead.mudon_sla_30min_fired = True
            else:
                lead.mudon_sla_1hour_fired = True
        return len(leads)

    # ─── First-contact detection ────────────────────────────────────
    def message_post(self, **kwargs):
        """When the assigned agent posts ANY chatter message on a
        Mudon lead, treat that as 'first contact logged' so SLA
        sweeps stop escalating.

        Skips system-generated posts (WA stub greeting, agent
        new-lead notification, SLA reminders) — those go through
        `_mudon_send_whatsapp` with `mudon_skip_first_contact` set.
        Without this guard the agent-self-create path silently
        disabled SLA escalation forever.
        """
        res = super().message_post(**kwargs)
        if self.env.context.get("mudon_skip_first_contact"):
            return res
        if self.mudon_pipeline_kind and not self.mudon_first_contact_logged:
            author = kwargs.get("author_id") or self.env.user.partner_id.id
            if self.user_id and self.user_id.partner_id.id == author:
                self.sudo().mudon_first_contact_logged = True
        return res
