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
    ("citizenship", "Citizenship"),
    ("goldenvisa", "Golden Visa"),
    ("investment", "Investment"),
]

CITY_SELECTION = [
    ("istanbul", "Istanbul"),
    ("trabzon", "Trabzon"),
    ("other_tr", "Other (Turkey)"),
    ("dubai", "Dubai"),
    ("abudhabi", "Abu Dhabi"),
    ("sharjah", "Sharjah"),
    ("rak", "Ras Al Khaimah"),
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
    ("citizenship", "Citizenship"),
    ("goldenvisa", "Golden Visa"),
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
    ("meta", "META"),
    ("google_ads", "Google Ads"),
    ("website", "Website"),
    ("whatsapp_direct", "WhatsApp Direct"),
    ("property_finder", "Property Finder"),
    ("bayut", "Bayut"),
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

# Stage 3
SERIOUSNESS_SELECTION = [
    ("serious", "Serious"),
    ("not_serious", "Not Serious"),
]

YESNO_SELECTION = [("yes", "Yes"), ("no", "No")]

# Stage 3 client survey (after 3rd offer)
SURVEY_PROP_OPTIONS = [
    ("suitable", "Suitable"),
    ("partially_suitable", "Partially Suitable"),
    ("not_suitable", "Not Suitable"),
]
SURVEY_STATUS = [
    ("interested", "Interested"),
    ("comparing", "Comparing"),
    ("not_ready", "Not Ready"),
    ("not_interested", "Not Interested"),
]
SURVEY_SUPPORT = [
    ("same_agent", "Same Agent"),
    ("different_agent", "Different Agent"),
    ("manager", "Manager"),
]

# Stage 4
CLIENT_TYPE_SELECTION = [("difficult", "Difficult"), ("easy", "Easy")]
PROPERTY_REQ_SELECTION = [
    ("available", "Available"),
    ("not_available", "Not Available"),
]

# Stage 6
HANDOVER_TYPE_SELECTION = [
    ("ready", "Ready"),
    ("off_plan", "Off-Plan"),
]

# Stage 7 — 2-level taxonomy held as one Selection (category_subreason)
LOST_REASON_SELECTION = [
    ("irrelevant_services", "Irrelevant Lead — Asking for services we don't offer"),
    ("irrelevant_country", "Irrelevant Lead — Wrong country"),
    ("irrelevant_spam", "Irrelevant Lead — Fake / spam"),
    ("unreachable_no_answer", "Not Reachable — No answer after 3 attempts"),
    ("unreachable_wrong_number", "Not Reachable — Wrong number"),
    ("low_intent_exploring", "Low Intent — Just exploring"),
    ("low_intent_no_urgency", "Low Intent — No urgency"),
    ("financial_budget_low", "Financial Mismatch — Budget too low"),
    ("financial_payment_plan", "Financial Mismatch — Payment plan unsuitable"),
    ("product_options", "Product Mismatch — Didn't like options"),
    ("product_location", "Product Mismatch — Location not suitable"),
]


# Map of stage transitions: (predicate_field, target_stage_xmlid_suffix)
STAGE_FLOW = [
    ("mudon_tick_offer_sent", "offer_sent"),
    ("mudon_visit_confirmed", "meeting"),
    ("mudon_paid_booking", "eoi"),
    ("mudon_fully_paid", "won"),
]


class CrmLead(models.Model):
    _inherit = "crm.lead"
    _order = "create_date desc, id desc"

    # ─── Pipeline marker ────────────────────────────────────────────
    mudon_pipeline_kind = fields.Selection(
        PIPELINE_KIND_SELECTION,
        string="Mudon Pipeline",
        compute="_compute_mudon_pipeline_kind",
        store=True,
        index=True,
    )

    # ─── STAGE 1: New Lead ──────────────────────────────────────────
    mudon_service = fields.Selection(SERVICE_SELECTION, string="MService")
    mudon_city = fields.Selection(CITY_SELECTION, string="MCity")
    mudon_city_other = fields.Char(string="Other City")
    mudon_priority = fields.Selection(
        PRIORITY_SELECTION, string="MPriority", default="normal",
    )
    mudon_budget = fields.Monetary(
        string="MBudget", currency_field="mudon_budget_currency_id",
    )
    mudon_budget_currency_id = fields.Many2one(
        "res.currency", default=lambda s: s.env.ref("base.USD").id,
    )
    mudon_status = fields.Selection(STATUS_SELECTION, string="Status")
    mudon_nationality_id = fields.Many2one("res.country", string="Nationality")
    mudon_living_in_id = fields.Many2one("res.country", string="Living In")
    mudon_in_country = fields.Boolean(string="In Country Now")
    mudon_purpose = fields.Selection(
        PURPOSE_SELECTION, string="Purpose of the Property",
    )
    mudon_property_type = fields.Selection(
        PROPERTY_TYPE_SELECTION, string="Property Type",
    )
    mudon_beds = fields.Integer(string="No. of Beds")
    mudon_other_specs = fields.Text(string="Other Specifications")
    mudon_visit_date = fields.Date(string="Expected Visit Date")
    mudon_notes = fields.Text(string="Notes")
    mudon_source = fields.Selection(SOURCE_SELECTION, string="Source")
    mudon_cbi_files = fields.Integer(string="No. of CBI Files")

    # SLA tracking — Stage 1 (30-min / 1-hour from spec)
    mudon_sla_30min_fired = fields.Boolean(copy=False)
    mudon_sla_1hour_fired = fields.Boolean(copy=False)
    mudon_first_contact_logged = fields.Boolean(copy=False)

    # ─── STAGE 2: Qualified ─────────────────────────────────────────
    mudon_tick_offer_sent = fields.Boolean(
        string="Offer Sent (tick)",
        copy=False,
        help="Ticking this advances the lead to Stage 3 — Offer Sent.",
    )
    mudon_branch_id = fields.Many2one(
        "mudon.branch",
        string="Branch",
        compute="_compute_mudon_branch_id",
        store=True,
    )
    mudon_card_color_hint = fields.Selection(
        [
            ("urgent_special", "Urgent + Citizenship/GV"),
            ("urgent_invest", "Urgent + Investment"),
            ("normal_special", "Normal + Citizenship/GV"),
            ("normal_invest", "Normal + Investment"),
            ("default", "Default"),
        ],
        compute="_compute_mudon_card_color_hint",
        store=True,
    )
    mudon_kanban_priority_rank = fields.Integer(
        compute="_compute_mudon_kanban_priority_rank",
        store=True,
        help="1..4 — drives kanban sort (P1 on top).",
    )
    mudon_qualified_entry_date = fields.Datetime(copy=False)
    mudon_sla_2hour_fired = fields.Boolean(copy=False)

    # ─── STAGE 3: Offer Sent ────────────────────────────────────────
    mudon_offer_counter = fields.Integer(
        string="Offer Counter",
        default=0,
        copy=False,
        help="Number of offers sent. Starts at 0; bumped to 1 on "
             "first MTick: Offer Sent. Max 10 per spec.",
    )
    mudon_last_offer_date = fields.Datetime(
        string="Last Offer Sent At", copy=False,
    )
    mudon_seriousness = fields.Selection(
        SERIOUSNESS_SELECTION, string="Seriousness",
    )
    mudon_visit_confirmed = fields.Selection(
        YESNO_SELECTION,
        string="MVisit Confirmed",
        copy=False,
        help="Set to 'yes' to advance to Stage 4 — Meeting.",
    )
    mudon_3rd_offer_survey_sent = fields.Boolean(copy=False)
    mudon_last_15day_reminder_date = fields.Date(copy=False)
    mudon_offer_3hr_reminder_idx = fields.Integer(
        default=0, copy=False,
        help="Last offer number that triggered its 3-hour-after WA "
             "reminder. Prevents re-firing for the same offer.",
    )

    # 3rd-offer survey responses
    mudon_survey_received_offer = fields.Selection(
        YESNO_SELECTION, string="Survey: Received Offer?",
    )
    mudon_survey_suitable = fields.Selection(
        YESNO_SELECTION, string="Survey: Was it Suitable?",
    )
    mudon_survey_needs_support = fields.Selection(
        YESNO_SELECTION, string="Survey: Needs Other Support?",
    )
    mudon_survey_property_options = fields.Selection(
        SURVEY_PROP_OPTIONS, string="Survey: Property Options",
    )
    mudon_survey_status = fields.Selection(
        SURVEY_STATUS, string="Survey: Status",
    )
    mudon_survey_preferred_support = fields.Selection(
        SURVEY_SUPPORT, string="Survey: Preferred Support",
    )

    # ─── STAGE 4: Meeting ───────────────────────────────────────────
    mudon_client_type = fields.Selection(
        CLIENT_TYPE_SELECTION, string="Client Type",
    )
    mudon_property_requirements = fields.Selection(
        PROPERTY_REQ_SELECTION, string="Property Requirements",
    )
    mudon_paid_booking = fields.Boolean(
        string="MPaid Booking",
        copy=False,
        help="Tick to advance to Stage 5 — EOI / Booking.",
    )
    mudon_meeting_reminder_3day_sent = fields.Boolean(copy=False)
    mudon_meeting_reminder_2day_sent = fields.Boolean(copy=False)
    mudon_meeting_reminder_1day_sent = fields.Boolean(copy=False)

    # ─── STAGE 5: EOI / Booking ─────────────────────────────────────
    mudon_fully_paid = fields.Boolean(
        string="MFully Paid",
        copy=False,
        help="Tick to advance to Stage 6 — WON (SPA Signed).",
    )

    # ─── STAGE 6: WON (SPA Signed) ──────────────────────────────────
    mudon_closing_amount = fields.Monetary(
        string="Closing Amount", currency_field="mudon_budget_currency_id",
    )
    mudon_developer_id = fields.Many2one(
        "mudon.developer", string="Developer",
    )
    mudon_project_name = fields.Char(string="Project Name")
    mudon_commission = fields.Monetary(
        string="Commission", currency_field="mudon_budget_currency_id",
    )
    mudon_handover_type = fields.Selection(
        HANDOVER_TYPE_SELECTION, string="Handover Type",
    )
    mudon_reason_to_win = fields.Text(string="Reason to Win")
    mudon_need_invoice = fields.Boolean(string="Need Invoice?")
    mudon_title_deed_required = fields.Boolean(string="Title Deed Required?")
    mudon_citizenship_required = fields.Boolean(
        string="Citizenship Required?",
        help="Turkey-only after-sales task.",
    )
    mudon_residence_required = fields.Boolean(
        string="Residence Required?",
        help="UAE-only after-sales task.",
    )
    mudon_furniture_required = fields.Boolean(
        string="Furniture / Other Service Required?",
    )
    mudon_admin_task_ids = fields.One2many(
        "mudon.admin.task", "lead_id", string="Admin Tasks",
    )
    mudon_after_sales_task_ids = fields.One2many(
        "mudon.after.sales.task", "lead_id", string="After-Sales Tasks",
    )

    # ─── STAGE 7: Lost ──────────────────────────────────────────────
    mudon_lost_reason = fields.Selection(
        LOST_REASON_SELECTION, string="Lost Reason",
    )

    # ─── Computes ───────────────────────────────────────────────────
    @api.depends("team_id")
    def _compute_mudon_pipeline_kind(self):
        turkey = self.env.ref(
            "mudon_crm.mudon_team_turkey", raise_if_not_found=False,
        )
        uae = self.env.ref(
            "mudon_crm.mudon_team_uae", raise_if_not_found=False,
        )
        for rec in self:
            if turkey and rec.team_id.id == turkey.id:
                rec.mudon_pipeline_kind = "turkey"
            elif uae and rec.team_id.id == uae.id:
                rec.mudon_pipeline_kind = "uae"
            else:
                rec.mudon_pipeline_kind = False

    @api.depends("team_id", "mudon_city")
    def _compute_mudon_branch_id(self):
        Branch = self.env["mudon.branch"].sudo()
        for rec in self:
            if not rec.team_id or not rec.mudon_city:
                rec.mudon_branch_id = False
                continue
            rec.mudon_branch_id = Branch.search([
                ("team_id", "=", rec.team_id.id),
                ("city_key", "=", rec.mudon_city),
            ], limit=1)

    @api.depends("mudon_priority", "mudon_service", "mudon_pipeline_kind")
    def _compute_mudon_card_color_hint(self):
        for rec in self:
            is_special = rec.mudon_service in ("citizenship", "goldenvisa")
            is_invest = rec.mudon_service == "investment"
            if rec.mudon_priority == "urgent" and is_special:
                rec.mudon_card_color_hint = "urgent_special"
            elif rec.mudon_priority == "urgent" and is_invest:
                rec.mudon_card_color_hint = "urgent_invest"
            elif rec.mudon_priority == "normal" and is_special:
                rec.mudon_card_color_hint = "normal_special"
            elif rec.mudon_priority == "normal" and is_invest:
                rec.mudon_card_color_hint = "normal_invest"
            else:
                rec.mudon_card_color_hint = "default"

    @api.depends("mudon_card_color_hint")
    def _compute_mudon_kanban_priority_rank(self):
        ranks = {
            "urgent_special": 1,
            "urgent_invest": 2,
            "normal_special": 3,
            "normal_invest": 4,
            "default": 5,
        }
        for rec in self:
            rec.mudon_kanban_priority_rank = ranks.get(
                rec.mudon_card_color_hint, 5,
            )

    # ─── Create / write: stage flow + funnel spawn ──────────────────
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

    def write(self, vals):
        """Drive stage transitions + side-effects from field flips.

        Order matters: we capture pre-values, call super, then look at
        what flipped TRUE → fire the matching transition. Keeps each
        transition idempotent (won't re-fire on a no-op write).

        Recursion guard: internal writes (advance_stage, counter bump,
        first_contact flip) carry `mudon_in_write=True` in context so
        the after-write hook short-circuits. Saves 5-8 redundant write
        transactions per business action.
        """
        if self.env.context.get("mudon_in_write"):
            return super().write(vals)
        pre = {
            r.id: {
                "stage_id": r.stage_id.id,
                "mudon_tick_offer_sent": r.mudon_tick_offer_sent,
                "mudon_visit_confirmed": r.mudon_visit_confirmed,
                "mudon_paid_booking": r.mudon_paid_booking,
                "mudon_fully_paid": r.mudon_fully_paid,
                "mudon_lost_reason": r.mudon_lost_reason,
                "mudon_offer_counter": r.mudon_offer_counter,
            }
            for r in self
        }
        res = super().write(vals)
        for rec in self:
            try:
                rec._mudon_after_write(pre.get(rec.id, {}), vals)
            except Exception as exc:
                _logger.warning(
                    "mudon_crm: after-write hook failed for lead %s: %s",
                    rec.id, exc,
                )
        return res

    def _mudon_after_write(self, prev, vals):
        """Per-lead side-effects after a write."""
        self.ensure_one()
        if not self.mudon_pipeline_kind:
            return

        # Stage 2 → Stage 3: Offer Sent
        if self.mudon_tick_offer_sent and not prev.get("mudon_tick_offer_sent"):
            self._mudon_advance_stage("offer_sent")
            # First offer: counter goes 0 → 1, stamp last_offer_date
            if self.mudon_offer_counter < 1:
                self.sudo().with_context(mudon_in_write=True).write({
                    "mudon_offer_counter": 1,
                    "mudon_last_offer_date": fields.Datetime.now(),
                    "mudon_offer_3hr_reminder_idx": 0,
                })

        # Stage 3 → Stage 4: Meeting (visit confirmed)
        if (self.mudon_visit_confirmed == "yes"
                and prev.get("mudon_visit_confirmed") != "yes"):
            self._mudon_advance_stage("meeting")
            self._mudon_notify_assigned_agent("meeting_entry", to_manager=True)

        # Stage 4 → Stage 5: EOI / Booking
        if self.mudon_paid_booking and not prev.get("mudon_paid_booking"):
            self._mudon_advance_stage("eoi")
            self._mudon_notify_assigned_agent(
                "eoi_entry", to_manager=True, to_agent=True,
            )

        # Stage 5 → Stage 6: WON
        if self.mudon_fully_paid and not prev.get("mudon_fully_paid"):
            self._mudon_advance_stage("won")
            self._mudon_spawn_admin_funnel()
            self._mudon_spawn_after_sales_funnel()
            self._mudon_notify_assigned_agent(
                "won_entry", to_manager=True, to_agent=True,
            )

        # Any → Stage 7: Lost
        if self.mudon_lost_reason and not prev.get("mudon_lost_reason"):
            self._mudon_advance_stage("lost")
            self._mudon_notify_marketing_lost()

        # Counter bump: if mudon_offer_counter increased
        new_counter = self.mudon_offer_counter
        old_counter = prev.get("mudon_offer_counter") or 0
        if new_counter > old_counter and new_counter > 1:
            self.sudo().with_context(mudon_in_write=True).write({
                "mudon_last_offer_date": fields.Datetime.now(),
                "mudon_offer_3hr_reminder_idx": old_counter,
            })

        # Stage 2 entry: fire qualified-client WA + reset SLA flags
        # so the 30-min / 2-hour Qualified crons restart their clock.
        prev_stage = prev.get("stage_id")
        if (self.stage_id
                and self.stage_id.id != prev_stage
                and self.stage_id.id in self._mudon_stage_ids("qualified")):
            self.sudo().with_context(mudon_in_write=True).write({
                "mudon_qualified_entry_date": fields.Datetime.now(),
                "mudon_sla_30min_fired": False,
                "mudon_sla_2hour_fired": False,
                "mudon_first_contact_logged": False,
            })
            self._mudon_notify_assigned_agent("qualified_entry")

    def _mudon_advance_stage(self, suffix):
        """Move the lead to mudon_stage_<pipeline>_<suffix>.

        If the target stage xmlid is missing (admin renamed or the
        data file failed to load), surface that loudly — log a
        warning AND drop a chatter note on the lead. Silent no-op
        would otherwise leave field staff confused when ticking the
        transition box appears to do nothing.
        """
        self.ensure_one()
        xmlid = "mudon_crm.mudon_stage_%s_%s" % (
            self.mudon_pipeline_kind, suffix,
        )
        stage = self.env.ref(xmlid, raise_if_not_found=False)
        if not stage:
            _logger.warning(
                "mudon_crm: target stage %s not found for lead %s — "
                "stage seed data may have been renamed or removed.",
                xmlid, self.id,
            )
            self.with_context(
                mudon_skip_first_contact=True,
            ).message_post(
                body=Markup(
                    "<p>Mudon: target stage <code>%s</code> not found "
                    "— staying on current stage. Check Settings → "
                    "Technical → External Identifiers.</p>"
                ) % escape(xmlid),
            )
            return
        if self.stage_id.id != stage.id:
            self.sudo().with_context(
                mudon_in_write=True,
            ).write({"stage_id": stage.id})

    # ─── Branch + country-code routing ──────────────────────────────
    def _mudon_auto_assign_agent(self):
        self.ensure_one()
        if self.user_id or not self.mudon_pipeline_kind:
            return
        agent = (
            self._mudon_pick_by_branch()
            or self._mudon_pick_by_country_code()
            or self._mudon_pick_round_robin()
            or self._mudon_pick_sales_manager()
        )
        if agent:
            self.write({"user_id": agent.id})

    def _mudon_pick_by_branch(self):
        """If the lead's city maps to a branch, round-robin in that
        branch. Implements the Stage 2 routing rule from the spec
        (Istanbul / Trabzon / Dubai)."""
        self.ensure_one()
        if not self.mudon_branch_id:
            return self.env["res.users"]
        return self.mudon_branch_id._pick_next_agent(
            exclude_lead_id=self.id,
        )

    def _mudon_pick_sales_manager(self):
        """Spec: 'If others, Assign to Sales Manager'."""
        self.ensure_one()
        if self.team_id.user_id:
            return self.team_id.user_id
        return self.env["res.users"]

    def _mudon_pick_by_country_code(self):
        self.ensure_one()
        prefix = self._mudon_phone_prefix()
        if not prefix:
            return self.env["res.users"]
        Mapping = self.env["mudon.country.agent.mapping"].sudo()
        m = Mapping.search([
            ("country_code", "=", prefix),
            ("team_id", "=", self.team_id.id),
        ], limit=1)
        if not m:
            m = Mapping.search([
                ("country_code", "=", prefix),
                ("team_id", "=", False),
            ], limit=1)
        return m.agent_user_id

    def _mudon_pick_round_robin(self):
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
        if not phone:
            return ""
        cleaned = re.sub(r"[^\d+]", "", phone)
        if cleaned.startswith("00"):
            cleaned = "+" + cleaned[2:]
        if "+" in cleaned:
            cleaned = "+" + cleaned.replace("+", "")
        return cleaned

    def _mudon_phone_prefix(self):
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

    # ─── Funnel spawn on WON ────────────────────────────────────────
    def _mudon_spawn_admin_funnel(self):
        self.ensure_one()
        AdminTask = self.env["mudon.admin.task"].sudo()
        AdminTask.create({
            "name": _("Admin: lead %s") % (self.name or self.contact_name or self.id),
            "lead_id": self.id,
            "need_invoice": self.mudon_need_invoice,
            "assigned_user_id": self.team_id.user_id.id or False,
        })

    def _mudon_spawn_after_sales_funnel(self):
        self.ensure_one()
        Task = self.env["mudon.after.sales.task"].sudo()
        owner = self.team_id.user_id.id or False
        leadname = self.name or self.contact_name or self.id
        if self.mudon_title_deed_required:
            Task.create({
                "name": _("Title Deed: %s") % leadname,
                "lead_id": self.id, "kind": "title_deed",
                "assigned_user_id": owner,
            })
        if (self.mudon_pipeline_kind == "turkey"
                and self.mudon_citizenship_required):
            Task.create({
                "name": _("Citizenship: %s") % leadname,
                "lead_id": self.id, "kind": "citizenship",
                "assigned_user_id": owner,
            })
        if (self.mudon_pipeline_kind == "uae"
                and self.mudon_residence_required):
            Task.create({
                "name": _("Residence: %s") % leadname,
                "lead_id": self.id, "kind": "residence",
                "assigned_user_id": owner,
            })
        if self.mudon_furniture_required:
            Task.create({
                "name": _("Furniture / Other: %s") % leadname,
                "lead_id": self.id, "kind": "furniture",
                "assigned_user_id": owner,
            })

    # ─── WhatsApp messaging (provider-agnostic) ─────────────────────
    def _mudon_send_whatsapp(self, phone, body, from_company=False):
        """Send a WA message. Provider chosen via
        `mudon_crm.wa_provider`. `from_company=True` uses the company
        number stored at `mudon_crm.wa_company_number` (Stage 3 client
        survey after the 3rd offer).
        """
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()
        provider = ICP.get_param("mudon_crm.wa_provider", "stub")
        company_no = ICP.get_param("mudon_crm.wa_company_number", "")
        normalized = self._mudon_phone_normalize(phone)
        sender_label = (
            "COMPANY (%s)" % (company_no or "no-number-configured")
            if from_company else "AGENT"
        )
        if provider == "stub":
            stub_body = Markup(
                "<p><b>[WA STUB · %s → %s]</b></p>%s"
            ) % (
                escape(sender_label),
                escape(normalized or "(no number)"),
                body,
            )
            self.with_context(mudon_skip_first_contact=True).message_post(
                body=stub_body,
                subject=_("WhatsApp (stub send)"),
            )
            return True
        _logger.warning(
            "mudon_crm: WA provider '%s' is configured but no concrete "
            "sender exists yet — message NOT sent for lead %s.",
            provider, self.id,
        )
        return False

    def _mudon_send_client_greeting(self):
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

    def _mudon_notify_assigned_agent(
        self, kind, to_manager=False, to_agent=True, to_marketing=False,
    ):
        self.ensure_one()
        # Build the standard 3-line block (Name + WA chat + Client Card).
        wa_link = "https://wa.me/%s" % (
            self._mudon_phone_normalize(self.phone).lstrip("+") or "",
        )
        base_url = self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "",
        )
        card_link = "%s/odoo/action-crm.crm_lead_action_pipeline/%s" % (
            base_url, self.id,
        )
        agent_name = self.user_id.name or "(unassigned)"
        headline_map = {
            "new_lead": _("You have new client!"),
            "qualified_entry": _("You have new qualified client!"),
            "sla_30min": _("You didn't contact the client!"),
            "sla_1hour": _("didn't contact client for 1 hrs."),
            "sla_qualified_30min": _("You didn't contact the client!"),
            "sla_qualified_2hour": _(
                "'%s' didn't contact client for 2 hrs."
            ) % agent_name,
            "offer_3hr": _(
                "Reminder! The offer was sent 3 hrs ago. "
                "Contact Client for Feedback"
            ),
            "offer_evd_15": _(
                "Reminder! Client visit is 15 days away! Confirm with Client"
            ),
            "offer_periodic_15": _("Stay in touch — don't let leads go cold."),
            "meeting_entry": _("Client in Meeting Stage!"),
            "meeting_t_minus_1": _("Client Meeting Reminder! 1 day away"),
            "meeting_t_minus_2": _("Client Meeting Reminder! 2 days away"),
            "meeting_t_minus_3": _("Client Meeting Reminder! 3 days away"),
            "eoi_entry": _("Client Booked EOI"),
            "won_entry": _("Congratulations! Client is WON"),
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
        if to_agent and self.user_id and self.user_id.phone:
            self._mudon_send_whatsapp(self.user_id.phone, body)
        if to_manager and self.team_id.user_id \
                and self.team_id.user_id.phone:
            self._mudon_send_whatsapp(self.team_id.user_id.phone, body)
        if to_marketing:
            # Marketing role = team manager fallback; client to configure
            # a dedicated marketing user via ir.config_parameter later.
            mkt_uid = int(self.env["ir.config_parameter"].sudo().get_param(
                "mudon_crm.marketing_user_id", "0",
            ) or 0)
            mkt = self.env["res.users"].browse(mkt_uid) if mkt_uid else False
            if mkt and mkt.phone:
                self._mudon_send_whatsapp(mkt.phone, body)
            elif self.team_id.user_id and self.team_id.user_id.phone:
                self._mudon_send_whatsapp(self.team_id.user_id.phone, body)

    def _mudon_notify_marketing_lost(self):
        self.ensure_one()
        reason_label = dict(LOST_REASON_SELECTION).get(
            self.mudon_lost_reason, "(no reason)",
        )
        detail_bits = [
            "Phone: %s" % (self.phone or ""),
            "Email: %s" % (self.email_from or ""),
            "City: %s" % (
                dict(CITY_SELECTION).get(self.mudon_city or "", "") or ""
            ),
            "Source: %s" % (
                dict(SOURCE_SELECTION).get(self.mudon_source or "", "") or ""
            ),
        ]
        body = Markup(
            "<p><b>%s</b></p>"
            "<p>Reason: %s</p>"
            "<p>Client Details:<br/>%s</p>"
        ) % (
            escape(_("Client Lost")),
            escape(reason_label),
            Markup("<br/>").join(escape(b) for b in detail_bits),
        )
        mkt_uid = int(self.env["ir.config_parameter"].sudo().get_param(
            "mudon_crm.marketing_user_id", "0",
        ) or 0)
        target = self.env["res.users"].browse(mkt_uid) if mkt_uid else False
        if not target:
            target = self.team_id.user_id
        if target and target.phone:
            self._mudon_send_whatsapp(target.phone, body)

    def _mudon_send_3rd_offer_survey(self):
        """Stage 3: after 3rd offer, WA the client from the COMPANY
        number with the structured survey."""
        self.ensure_one()
        if not self.phone:
            return
        body = Markup(
            "<p><b>Quick check on the offers we sent you:</b></p>"
            "<p>1. Did you receive an offer? Yes / No<br/>"
            "2. Was it suitable? Yes / No<br/>"
            "3. Do you need support from another agent or management? "
            "Yes / No</p>"
            "<p><b>Property options</b><br/>"
            "☐ Suitable ☐ Partially Suitable ☐ Not Suitable</p>"
            "<p><b>Status</b><br/>"
            "☐ Interested ☐ Comparing ☐ Not Ready ☐ Not Interested</p>"
            "<p><b>Preferred support</b><br/>"
            "☐ Same Agent ☐ Different Agent ☐ Manager</p>"
        )
        self._mudon_send_whatsapp(self.phone, body, from_company=True)

    # ─── SLA cron handlers ──────────────────────────────────────────
    @api.model
    def _mudon_cron_sla_30min(self):
        """Stage 1 — 30-min reminder if no agent contact."""
        threshold = fields.Datetime.now() - timedelta(minutes=30)
        leads = self.search([
            ("create_date", "<=", threshold),
            ("mudon_first_contact_logged", "=", False),
            ("mudon_sla_30min_fired", "=", False),
            ("mudon_pipeline_kind", "in", ("turkey", "uae")),
            ("stage_id.id", "in", self._mudon_stage_ids("new_lead")),
        ])
        for lead in leads:
            lead._mudon_notify_assigned_agent("sla_30min")
            lead.mudon_sla_30min_fired = True
        return len(leads)

    @api.model
    def _mudon_cron_sla_1hour(self):
        """Stage 1 — 1-hour escalation to agent + manager."""
        threshold = fields.Datetime.now() - timedelta(minutes=60)
        leads = self.search([
            ("create_date", "<=", threshold),
            ("mudon_first_contact_logged", "=", False),
            ("mudon_sla_1hour_fired", "=", False),
            ("mudon_pipeline_kind", "in", ("turkey", "uae")),
            ("stage_id.id", "in", self._mudon_stage_ids("new_lead")),
        ])
        for lead in leads:
            lead._mudon_notify_assigned_agent(
                "sla_1hour", to_manager=True,
            )
            lead.mudon_sla_1hour_fired = True
        return len(leads)

    @api.model
    def _mudon_cron_qualified_30min(self):
        threshold = fields.Datetime.now() - timedelta(minutes=30)
        leads = self.search([
            ("mudon_qualified_entry_date", "<=", threshold),
            ("mudon_first_contact_logged", "=", False),
            ("mudon_sla_30min_fired", "=", False),
            ("stage_id.id", "in", self._mudon_stage_ids("qualified")),
        ])
        for lead in leads:
            lead._mudon_notify_assigned_agent("sla_qualified_30min")
            lead.mudon_sla_30min_fired = True
        return len(leads)

    @api.model
    def _mudon_cron_qualified_2hour(self):
        threshold = fields.Datetime.now() - timedelta(hours=2)
        leads = self.search([
            ("mudon_qualified_entry_date", "<=", threshold),
            ("mudon_first_contact_logged", "=", False),
            ("mudon_sla_2hour_fired", "=", False),
            ("stage_id.id", "in", self._mudon_stage_ids("qualified")),
        ])
        for lead in leads:
            lead._mudon_notify_assigned_agent(
                "sla_qualified_2hour", to_manager=True,
            )
            lead.mudon_sla_2hour_fired = True
        return len(leads)

    @api.model
    def _mudon_cron_offer_3hour(self):
        """Stage 3 — 3hr after each offer was sent → 'contact for
        feedback' to the agent. Only fires once per offer increment."""
        threshold = fields.Datetime.now() - timedelta(hours=3)
        leads = self.search([
            ("mudon_last_offer_date", "<=", threshold),
            ("mudon_offer_counter", ">", 0),
            ("stage_id.id", "in", self._mudon_stage_ids("offer_sent")),
        ])
        fired = 0
        for lead in leads:
            if lead.mudon_offer_3hr_reminder_idx >= lead.mudon_offer_counter:
                continue
            lead._mudon_notify_assigned_agent("offer_3hr")
            lead.mudon_offer_3hr_reminder_idx = lead.mudon_offer_counter
            fired += 1
        return fired

    @api.model
    def _mudon_cron_offer_evd_15days_before(self):
        """Stage 3 — 15 days BEFORE the expected visit date."""
        target = fields.Date.today() + timedelta(days=15)
        leads = self.search([
            ("mudon_visit_date", "=", target),
            ("stage_id.id", "in", self._mudon_stage_ids("offer_sent")),
        ])
        for lead in leads:
            lead._mudon_notify_assigned_agent("offer_evd_15")
        return len(leads)

    @api.model
    def _mudon_cron_offer_periodic_15days(self):
        """Stage 3 — every 15 days while (EVD − today) > 15."""
        today = fields.Date.today()
        leads = self.search([
            ("mudon_visit_date", ">", today + timedelta(days=15)),
            ("stage_id.id", "in", self._mudon_stage_ids("offer_sent")),
        ])
        fired = 0
        for lead in leads:
            last = lead.mudon_last_15day_reminder_date
            if last and (today - last).days < 15:
                continue
            lead._mudon_notify_assigned_agent("offer_periodic_15")
            lead.mudon_last_15day_reminder_date = today
            fired += 1
        return fired

    @api.model
    def _mudon_cron_offer_3rd_survey(self):
        """Stage 3 — after 3rd offer sent, send the structured client
        survey from the Company WA number. One-shot per lead."""
        leads = self.search([
            ("mudon_offer_counter", ">=", 3),
            ("mudon_3rd_offer_survey_sent", "=", False),
            ("stage_id.id", "in", self._mudon_stage_ids("offer_sent")),
        ])
        for lead in leads:
            lead._mudon_send_3rd_offer_survey()
            lead.mudon_3rd_offer_survey_sent = True
        return len(leads)

    @api.model
    def _mudon_cron_meeting_reminders(self):
        """Stage 4 — T-3 / T-2 / T-1 day meeting reminders."""
        today = fields.Date.today()
        fired = 0
        for days, flag in (
            (3, "mudon_meeting_reminder_3day_sent"),
            (2, "mudon_meeting_reminder_2day_sent"),
            (1, "mudon_meeting_reminder_1day_sent"),
        ):
            target = today + timedelta(days=days)
            leads = self.search([
                ("mudon_visit_date", "=", target),
                (flag, "=", False),
                ("stage_id.id", "in", self._mudon_stage_ids("meeting")),
            ])
            kind = "meeting_t_minus_%d" % days
            for lead in leads:
                lead._mudon_notify_assigned_agent(kind)
                lead.sudo().write({flag: True})
                fired += 1
        return fired

    @api.model
    def _read_group_stage_ids(self, stages, domain):
        """Hide Odoo's global default stages (New / Qualified /
        Proposition / Won) from the Mudon pipeline kanbans. Standard
        behavior pools `team_ids = empty` (global) + `team_ids = this
        team` stages — for Mudon teams the client only wants their
        7 seeded stages, not the 4 defaults that would otherwise
        appear alongside (giving 11 columns).

        Falls through to standard behavior for non-Mudon teams so
        other CRM users aren't affected.
        """
        team_id = self._context.get("default_team_id")
        if team_id:
            turkey = self.env.ref(
                "mudon_crm.mudon_team_turkey", raise_if_not_found=False,
            )
            uae = self.env.ref(
                "mudon_crm.mudon_team_uae", raise_if_not_found=False,
            )
            mudon_ids = [t.id for t in (turkey, uae) if t]
            if team_id in mudon_ids:
                return stages.search(
                    [("team_ids", "=", team_id)],
                    order=stages._order,
                )
        if hasattr(super(), "_read_group_stage_ids"):
            return super()._read_group_stage_ids(stages, domain)
        return stages.search(domain, order=stages._order)

    @api.model
    def _mudon_stage_ids(self, suffix):
        """Return both Turkey + UAE stage ids for a given suffix.

        Cached on the env to avoid an `env.ref` lookup per cron call.
        Stages are seeded with `noupdate=1` so the xmlid stability
        across upgrades is guaranteed.
        """
        ids = []
        for kind in ("turkey", "uae"):
            stage = self.env.ref(
                "mudon_crm.mudon_stage_%s_%s" % (kind, suffix),
                raise_if_not_found=False,
            )
            if stage:
                ids.append(stage.id)
        return ids

    # Stage-2 entry timestamp + SLA flag reset is handled inside
    # `_mudon_after_write` — keeping it out of `_track_subtype` avoids
    # writing tracked fields during Odoo's tracking pipeline, which
    # would re-enter our own write() and cascade.

    # ─── Inbound WA webhook handler ─────────────────────────────────
    @api.model
    def _mudon_handle_inbound_wa_message(self, sender_phone, body):
        """Dispatch an inbound WA message.

        - Try to match the sender to an Agent (res.users.phone) — if
          matched, look at their currently-assigned lead pool and apply
          any command keyword in `body` to the most recently updated
          card.
        - Else try to match the sender to a Lead by phone — log the
          body to the lead's chatter.
        """
        sender_norm = self._mudon_phone_normalize(sender_phone)
        body_l = (body or "").strip().lower()

        # Sender = Agent?
        agent = self.env["res.users"].sudo().search([
            ("phone", "ilike", sender_norm.lstrip("+")),
        ], limit=1) if sender_norm else False
        if agent:
            lead = self.sudo().search([
                ("user_id", "=", agent.id),
                ("mudon_pipeline_kind", "in", ("turkey", "uae")),
            ], order="write_date desc", limit=1)
            if lead and self._mudon_is_offer_sent_command(body_l):
                # On Stage 2 → set the tick; on Stage 3 → bump counter
                if lead.stage_id.id in lead._mudon_stage_ids("qualified"):
                    lead.write({"mudon_tick_offer_sent": True})
                elif lead.stage_id.id in lead._mudon_stage_ids("offer_sent"):
                    lead.write({
                        "mudon_offer_counter": min(
                            lead.mudon_offer_counter + 1, 10,
                        ),
                    })
                return True
            if lead:
                lead.with_context(
                    mudon_skip_first_contact=True,
                ).message_post(
                    body=Markup("<p><b>WA inbound (Agent):</b> %s</p>")
                         % escape(body),
                )
            return True

        # Sender = Lead phone?
        lead = self.sudo().search([
            ("phone", "ilike", sender_norm.lstrip("+")),
            ("mudon_pipeline_kind", "in", ("turkey", "uae")),
        ], limit=1) if sender_norm else False
        if lead:
            lead.with_context(
                mudon_skip_first_contact=True,
            ).message_post(
                body=Markup("<p><b>WA inbound (Client):</b> %s</p>")
                     % escape(body),
            )
            return True
        _logger.info(
            "mudon_crm: inbound WA from %s did not match any agent/lead "
            "(body=%r)", sender_phone, body,
        )
        return False

    @staticmethod
    def _mudon_is_offer_sent_command(body_l):
        for kw in (
            "offer sent", "offersent", "send offer", "offer was sent",
        ):
            if kw in body_l:
                return True
        return False

    # ─── First-contact detection ────────────────────────────────────
    def message_post(self, **kwargs):
        res = super().message_post(**kwargs)
        if self.env.context.get("mudon_skip_first_contact"):
            return res
        if self.mudon_pipeline_kind and not self.mudon_first_contact_logged:
            author = kwargs.get("author_id") or self.env.user.partner_id.id
            if self.user_id and self.user_id.partner_id.id == author:
                self.sudo().with_context(
                    mudon_in_write=True,
                ).write({"mudon_first_contact_logged": True})
        return res
