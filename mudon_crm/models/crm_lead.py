import logging
import re
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


PIPELINE_KIND_SELECTION = [
    ("turkey", "Turkey CBI / Investment"),
    ("uae", "UAE Dubai Golden Visa / Investment"),
]

# v19.0.1.2.0 — the lists previously here as module constants
# (SERVICE_SELECTION, CITY_SELECTION, STATUS_SELECTION, PURPOSE_SELECTION,
# PROPERTY_TYPE_SELECTION, SOURCE_SELECTION, LOST_REASON_SELECTION) now
# live as admin-managed master tables:
#   mudon.service / mudon.city / mudon.lead.status / mudon.purpose /
#   mudon.property.type / mudon.source / mudon.lost.reason
# Their seeded `code` fields match the old Selection keys 1-for-1 so the
# automation rules below (kanban color, sort priority, transitions) keep
# the same code-based identity checks (e.g. `rec.mudon_service_id.code
# == "citizenship"`). Admins rename `name` freely; don't touch `code`.

PRIORITY_SELECTION = [
    ("urgent", "Urgent"),
    ("normal", "Normal"),
]

# v19.0.1.5.0 — funnel order + each stage's MANDATORY field(s). Moving a
# lead forward requires every crossed stage's mandatory field(s); when a
# card is dragged PAST one or more stages the quick-fill wizard collects /
# confirms them all (client: "New → Meeting must ask for Qualified +
# Offer Sent + Meeting, not only the first stage"). Lost is out-of-band
# (see _mudon_check_stage_transitions).
MUDON_STAGE_ORDER = (
    "new_lead", "qualified", "offer_sent", "meeting", "eoi", "won",
)
MUDON_QUALIFY_DATA = (
    "mudon_service_id", "mudon_city_id", "mudon_priority", "expected_revenue",
)
MUDON_STAGE_REQUIRED = {
    "qualified": MUDON_QUALIFY_DATA,
    "offer_sent": ("mudon_tick_offer_sent",),
    "meeting": ("mudon_visit_confirmed",),
    "eoi": ("mudon_paid_booking",),
    "won": ("mudon_fully_paid",),
}
MUDON_STAGE_FIELD_LABELS = {
    "mudon_service_id": "Service",
    "mudon_city_id": "City",
    "mudon_priority": "Priority",
    "expected_revenue": "Expected Revenue",
    "mudon_tick_offer_sent": "Offer Sent",
    "mudon_visit_confirmed": "Visit Confirmed",
    "mudon_paid_booking": "Paid Booking",
    "mudon_fully_paid": "Fully Paid",
}

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

# v19.0.1.2.0 — old LOST_REASON_SELECTION lifted into the
# `mudon.lost.reason` hierarchical master (5 categories × 2-3
# subreasons each, seeded with the same `code` values).


# Map of stage transitions: (predicate_field, target_stage_xmlid_suffix)
STAGE_FLOW = [
    ("mudon_tick_offer_sent", "offer_sent"),
    ("mudon_visit_confirmed", "meeting"),
    ("mudon_paid_booking", "eoi"),
    ("mudon_fully_paid", "won"),
]


class CrmLead(models.Model):
    _inherit = "crm.lead"
    # Sort order applies to every kanban column on every team.
    # 1. priority rank — P1 Citizenship+Urgent → P4 Investment+Normal
    # 2. nearest expected visit date — relevant on Offer Sent + Meeting
    # 3. most recent on top (the New Lead default per spec)
    # Non-Mudon teams have null on all Mudon fields → sort collapses
    # to the standard create_date desc behaviour for them.
    _order = (
        "mudon_kanban_priority_rank asc, "
        "mudon_visit_date asc nulls last, "
        "create_date desc, id desc"
    )

    # ─── Pipeline marker ────────────────────────────────────────────
    mudon_pipeline_kind = fields.Selection(
        PIPELINE_KIND_SELECTION,
        string="Mudon Pipeline",
        compute="_compute_mudon_pipeline_kind",
        store=True,
        index=True,
    )

    # Related field so view-level `required="..."` / `invisible="..."`
    # expressions can branch on the current stage's semantic kind.
    mudon_stage_kind_current = fields.Selection(
        related="stage_id.mudon_stage_kind",
        store=True,
        readonly=True,
        index=True,
        string="Stage Kind (current)",
    )

    # ─── STAGE 1: New Lead ──────────────────────────────────────────
    # v19.0.1.2.0 — Selection enums replaced by admin-managed masters
    # (see comment block at top of file). Field rename adds the _id /
    # _ids suffix so the schema type is obvious at sight.
    mudon_service_id = fields.Many2one(
        "mudon.service", string="Service", ondelete="restrict",
    )
    # Stored mirror of the service `code` so the kanban template can do
    # code-based identity checks (a Many2one's raw_value is the id, not
    # the code) — e.g. badge colour for citizenship / goldenvisa / investment.
    mudon_service_code = fields.Char(
        related="mudon_service_id.code", store=True,
    )
    mudon_city_id = fields.Many2one(
        "mudon.city",
        string="City",
    )
    mudon_city_other = fields.Char(string="Other City")
    mudon_show_city_other = fields.Boolean(
        compute="_compute_mudon_show_city_other",
        help="True if the customer picked the 'Other (Turkey)' city tag — "
             "drives visibility of the free-text city field on the form.",
    )
    mudon_priority = fields.Selection(
        PRIORITY_SELECTION, string="Priority", default="normal",
    )
    mudon_budget = fields.Monetary(
        string="Budget", currency_field="mudon_budget_currency_id",
    )
    mudon_budget_currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        compute="_compute_mudon_budget_currency",
        store=True, readonly=True,
        help="Per-pipeline currency — UAE Dubai = AED, Turkey = USD. "
             "Drives MBudget, Closing Amount and Commission.",
    )
    mudon_status_id = fields.Many2one(
        "mudon.lead.status", string="Status", ondelete="restrict",
    )
    mudon_nationality_id = fields.Many2one("res.country", string="Nationality")
    mudon_living_in_id = fields.Many2one("res.country", string="Living In")
    mudon_in_country = fields.Boolean(string="In Country Now")
    mudon_purpose_ids = fields.Many2many(
        "mudon.purpose", "crm_lead_mudon_purpose_rel",
        "lead_id", "purpose_id",
        string="Purpose of the Property",
    )
    mudon_property_type_ids = fields.Many2many(
        "mudon.property.type", "crm_lead_mudon_property_type_rel",
        "lead_id", "property_type_id",
        string="Property Type",
    )
    mudon_beds = fields.Integer(string="No. of Beds")
    mudon_other_specs = fields.Text(string="Other Specifications")
    mudon_visit_date = fields.Date(string="Expected Visit Date")
    mudon_notes = fields.Text(string="Notes")
    mudon_source_id = fields.Many2one(
        "mudon.source", string="Source", ondelete="restrict",
    )
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
    mudon_visit_confirmed = fields.Boolean(
        string="Visit Confirmed",
        copy=False,
        help="Tick to advance to Stage 4 — Meeting.",
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
        string="Paid Booking",
        copy=False,
        help="Tick to advance to Stage 5 — EOI / Booking.",
    )
    mudon_meeting_reminder_3day_sent = fields.Boolean(copy=False)
    mudon_meeting_reminder_2day_sent = fields.Boolean(copy=False)
    mudon_meeting_reminder_1day_sent = fields.Boolean(copy=False)

    # ─── STAGE 5: EOI / Booking ─────────────────────────────────────
    mudon_fully_paid = fields.Boolean(
        string="Fully Paid",
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
    mudon_project_id = fields.Many2one(
        "mudon.project", string="Project", ondelete="restrict",
    )
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
    mudon_lost_reason_id = fields.Many2one(
        "mudon.lost.reason",
        string="Lost Reason",
        domain="[('is_category', '=', False)]",
        ondelete="restrict",
        help="Pick a subreason (rows whose category isn't empty). "
             "Category-only rows are not selectable as a lost reason "
             "on a lead.",
    )

    # ─── Computes ───────────────────────────────────────────────────
    @api.depends("mudon_city_id")
    def _compute_mudon_show_city_other(self):
        for rec in self:
            rec.mudon_show_city_other = (
                rec.mudon_city_id and rec.mudon_city_id.code == "other_tr"
            )

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

    @api.depends("mudon_pipeline_kind")
    def _compute_mudon_budget_currency(self):
        """Per-pipeline currency: UAE Dubai bills in AED, Turkey in USD.
        Non-Mudon (or unresolved) leads fall back to USD. Stored so the
        monetary widgets (MBudget / Closing Amount / Commission) render
        each lead's amounts in its own pipeline currency."""
        aed = self.env.ref("base.AED", raise_if_not_found=False)
        usd = self.env.ref("base.USD", raise_if_not_found=False)
        fallback = usd or self.env.company.currency_id
        for rec in self:
            if rec.mudon_pipeline_kind == "uae" and aed:
                rec.mudon_budget_currency_id = aed
            else:
                rec.mudon_budget_currency_id = fallback

    @api.depends("team_id", "mudon_city_id")
    def _compute_mudon_branch_id(self):
        """Match the selected city's `code` to a branch on this team. If
        no match (or the city is "Others"), no branch — auto-assignment
        then falls through to the country-code mapping or round-robin
        chain in `_mudon_auto_assign_agent`.
        """
        Branch = self.env["mudon.branch"].sudo()
        for rec in self:
            if not rec.team_id or not rec.mudon_city_id:
                rec.mudon_branch_id = False
                continue
            rec.mudon_branch_id = Branch.search([
                ("team_id", "=", rec.team_id.id),
                ("city_key", "=", rec.mudon_city_id.code),
            ], limit=1)

    @api.depends("mudon_priority", "mudon_service_id.code", "mudon_pipeline_kind")
    def _compute_mudon_card_color_hint(self):
        for rec in self:
            service_code = rec.mudon_service_id.code if rec.mudon_service_id else False
            is_special = service_code in ("citizenship", "goldenvisa")
            is_invest = service_code == "investment"
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

    # v19.0.1.4.0 — `mudon_city_ids` (Many2many tags) reduced to
    # `mudon_city_id` (Many2one) so branch round-robin routing has one
    # definitive city per lead. Client feedback: multi-city selection
    # broke the routing logic.
    MUDON_REQUIRED_TO_QUALIFY = (
        "mudon_service_id",
        "mudon_city_id",
        "mudon_priority",
        "mudon_budget",
    )

    def _mudon_crossed_required(self, current_kind, target_kind):
        """Ordered mandatory field names for every funnel stage strictly
        after `current_kind` up to and including `target_kind`. Empty for
        a backward / same-stage move or a Lost target."""
        order = MUDON_STAGE_ORDER
        if target_kind not in order:
            return []
        ti = order.index(target_kind)
        ci = order.index(current_kind) if current_kind in order else -1
        req = []
        for kind in order[ci + 1:ti + 1]:
            req.extend(MUDON_STAGE_REQUIRED.get(kind, ()))
        return req

    def _mudon_check_stage_requirements(self, new_stage, vals=None):
        """Gate a forward funnel move on EVERY crossed stage's mandatory
        field(s) and route the user to the quick-fill wizard if any are
        missing.

        - Single-step advance: only the qualify DATA fields (Service /
          City / Priority / Budget) are hard-gated — a lone stage tick is
          auto-confirmed by the drag itself (see write()).
        - Skipping stages: the user must fill / confirm every crossed
          stage's mandatory field, so a card dragged New → Meeting is
          asked for Qualified + Offer Sent + Meeting in one wizard.

        Lost is handled separately by _mudon_check_stage_transitions.
        """
        vals = vals or {}
        if not new_stage or not new_stage.mudon_stage_kind:
            return
        target_kind = new_stage.mudon_stage_kind
        if target_kind not in MUDON_STAGE_ORDER:
            return
        from odoo.exceptions import RedirectWarning
        ti = MUDON_STAGE_ORDER.index(target_kind)
        for rec in self:
            if not rec.mudon_pipeline_kind:
                continue
            ck = rec.mudon_stage_kind_current
            ci = MUDON_STAGE_ORDER.index(ck) if ck in MUDON_STAGE_ORDER else -1
            if ti <= ci:
                continue  # backward / same stage → no gate
            crossed = rec._mudon_crossed_required(ck, target_kind)
            if (ti - ci) > 1:
                # skipping stages → confirm the full crossed set
                required = [
                    f for f in crossed if not (rec[f] or vals.get(f))
                ]
            else:
                # single-step advance → only the qualify DATA is hard-gated
                required = [
                    f for f in crossed
                    if f in MUDON_QUALIFY_DATA and not (rec[f] or vals.get(f))
                ]
            if not required:
                continue
            action = self.env["ir.actions.act_window"]._for_xml_id(
                "mudon_crm.action_mudon_quick_fill_wizard",
            )
            action["context"] = {
                "default_lead_id": rec.id,
                "default_target_stage_id": new_stage.id,
            }
            raise RedirectWarning(
                _("Missing: %s. Fill them to move %s forward.") % (
                    ", ".join(
                        MUDON_STAGE_FIELD_LABELS.get(f, f) for f in required
                    ),
                    rec.name or rec.contact_name or _("this lead"),
                ),
                action,
                _("Fill Required Fields"),
            )

    # Stages where the drag-drop itself is the semantic action, so we
    # auto-tick the corresponding field rather than blocking the user.
    # The auto-tick is applied inside write() by mutating `vals` before
    # super() is called; the after-write hook then sees the flip and
    # fires the usual side-effects (notifications, counter bumps).
    MUDON_AUTOTICK_GATES = {
        "offer_sent": "mudon_tick_offer_sent",
        "meeting": "mudon_visit_confirmed",
        "eoi": "mudon_paid_booking",
        "won": "mudon_fully_paid",
    }

    def _mudon_check_stage_transitions(self, new_stage, vals=None):
        """Only Lost still needs a hard gate — the reason can't be
        auto-picked. The other 4 stage kinds are handled by the
        auto-tick path in write().

        On Lost with no Lost Reason set, we throw a RedirectWarning
        pointing to the quick-fill wizard so the user picks a reason
        inline (matches client feedback: 'no popup invalid, show me
        the form to fulfil the condition').
        """
        if not new_stage or not new_stage.mudon_stage_kind:
            return
        if new_stage.mudon_stage_kind != "lost":
            return
        from odoo.exceptions import RedirectWarning
        for rec in self:
            if not rec.mudon_pipeline_kind:
                continue
            if rec.mudon_lost_reason_id or (vals or {}).get("mudon_lost_reason_id"):
                continue
            action = self.env["ir.actions.act_window"]._for_xml_id(
                "mudon_crm.action_mudon_quick_fill_wizard",
            )
            action["context"] = {
                "default_lead_id": rec.id,
                "default_target_stage_id": new_stage.id,
            }
            raise RedirectWarning(
                _("Pick a Lost Reason to mark %s as Lost.") % (
                    rec.name or rec.contact_name or _("this lead"),
                ),
                action,
                _("Pick Lost Reason"),
            )

    def write(self, vals):
        """Drive stage transitions + side-effects from field flips.

        Order matters: we capture pre-values, call super, then look at
        what flipped TRUE → fire the matching transition. Keeps each
        transition idempotent (won't re-fire on a no-op write).

        Recursion guard: internal writes (advance_stage, counter bump,
        first_contact flip) carry `mudon_in_write=True` in context so
        the after-write hook short-circuits. Saves 5-8 redundant write
        transactions per business action.

        Validation: a user-driven `stage_id` change OUT of New Lead
        with any of MService / MCity / MPriority / MBudget unset opens
        the quick-fill wizard (via RedirectWarning) instead of blocking
        with a raw error — client feedback: fill the missing fields
        inline so the drag isn't wasted. Internal stage advances (via
        `mudon_in_write` context) are already protected because those
        only fire when our own field flips happen — which presume the
        lead is past New Lead.
        """
        if self.env.context.get("mudon_in_write"):
            return super().write(vals)
        if "stage_id" in vals and vals["stage_id"]:
            new_stage = self.env["crm.stage"].sudo().browse(vals["stage_id"])
            self._mudon_check_stage_requirements(new_stage, vals)
            self._mudon_check_stage_transitions(new_stage, vals)
            # Auto-tick the transition field ONLY for a single-step
            # forward drag into offer_sent / meeting / eoi / won — the
            # drag IS the confirmation. A multi-stage SKIP does NOT
            # auto-tick: the quick-fill wizard collects every crossed
            # stage's confirmation instead. The after-write hook then
            # detects the flip and fires the side-effects exactly once.
            kind = new_stage.mudon_stage_kind
            tick_field = self.MUDON_AUTOTICK_GATES.get(kind)
            if tick_field and tick_field not in vals:
                ti = (MUDON_STAGE_ORDER.index(kind)
                      if kind in MUDON_STAGE_ORDER else -1)
                needs_tick = any(
                    rec.mudon_pipeline_kind and not rec[tick_field]
                    and ti >= 0
                    and ti - (
                        MUDON_STAGE_ORDER.index(rec.mudon_stage_kind_current)
                        if rec.mudon_stage_kind_current in MUDON_STAGE_ORDER
                        else -1
                    ) == 1
                    for rec in self
                )
                if needs_tick:
                    vals[tick_field] = True
        pre = {
            r.id: {
                "stage_id": r.stage_id.id,
                "mudon_tick_offer_sent": r.mudon_tick_offer_sent,
                "mudon_visit_confirmed": r.mudon_visit_confirmed,
                "mudon_paid_booking": r.mudon_paid_booking,
                "mudon_fully_paid": r.mudon_fully_paid,
                "mudon_lost_reason_id": r.mudon_lost_reason_id.id,
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
        if (self.mudon_visit_confirmed
                and not prev.get("mudon_visit_confirmed")):
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
        if (self.mudon_lost_reason_id.id
                and not prev.get("mudon_lost_reason_id")):
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

    def _mudon_advance_stage(self, kind):
        """Move the lead to the stage in THIS lead's team whose
        `mudon_stage_kind` equals `kind`.

        Looks up by (team_id, mudon_stage_kind) — no xmlid coupling.
        Adding a new Mudon pipeline (e.g. KSA) needs no code change:
        just seed its 7 stages with the right `mudon_stage_kind`
        values and transitions Just Work.

        Surfaces missing stages loudly via warning + chatter note so
        field staff don't see ticks silently no-op.
        """
        self.ensure_one()
        if not self.team_id:
            return
        # Forward-only: never let an after-write tick advance pull the
        # lead BACK to an earlier funnel stage (a skip-drag can set
        # several ticks at once). Compare against the live stage_id, not
        # the stored-computed kind, which may not be recomputed yet.
        current_kind = self.stage_id.mudon_stage_kind
        if (kind in MUDON_STAGE_ORDER and current_kind in MUDON_STAGE_ORDER
                and MUDON_STAGE_ORDER.index(kind)
                <= MUDON_STAGE_ORDER.index(current_kind)):
            return
        stage = self.env["crm.stage"].sudo().search([
            ("team_ids", "=", self.team_id.id),
            ("mudon_stage_kind", "=", kind),
        ], limit=1)
        if not stage:
            _logger.warning(
                "mudon_crm: no stage with kind=%s on team %s for lead %s",
                kind, self.team_id.name, self.id,
            )
            self.with_context(
                mudon_skip_first_contact=True,
            ).message_post(
                body=Markup(
                    "<p>Mudon: no stage with kind <code>%s</code> on "
                    "team <code>%s</code> — staying on current stage.</p>"
                ) % (escape(kind), escape(self.team_id.name or "")),
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
            self._mudon_wa_log(normalized, body, status="stub",
                               from_company=from_company)
            return True
        if provider == "meta":
            return self._mudon_send_whatsapp_meta(
                phone, body, from_company=from_company)
        _logger.warning(
            "mudon_crm: unknown WA provider '%s' — message NOT sent for "
            "lead %s.", provider, self.id,
        )
        return False

    # ─── Meta Cloud API send + delivery log + retry ─────────────────
    def _mudon_wa_log(self, to_number, body, status="sent", wamid="",
                      error="", from_company=False, direction="out"):
        """Append one row to the WhatsApp message log (audit + retry
        queue). Body is stored as plain text."""
        try:
            text = html2plaintext(body) if body else ""
        except Exception:
            text = str(body or "")
        try:
            return self.env["mudon.wa.message"].sudo().create({
                "lead_id": self.id if self else False,
                "direction": direction,
                "to_number": to_number or "",
                "from_company": from_company,
                "body": text,
                "status": status,
                "wamid": wamid or False,
                "error": error or False,
            })
        except Exception as exc:
            _logger.warning("mudon_crm: WA log write failed: %s", exc)
            return self.env["mudon.wa.message"]

    def _mudon_wa_meta_post(self, to_digits, text, from_company=False):
        """Low-level POST of one text message to the Meta Cloud API.
        Returns (ok, wamid, error, permanent). No DB writes — callers
        log the result."""
        ICP = self.env["ir.config_parameter"].sudo()
        token = ICP.get_param("mudon_crm.wa_access_token", "")
        api_version = ICP.get_param("mudon_crm.wa_api_version", "v21.0") or "v21.0"
        if from_company:
            phone_number_id = (
                ICP.get_param("mudon_crm.wa_company_phone_number_id", "")
                or ICP.get_param("mudon_crm.wa_phone_number_id", "")
            )
        else:
            phone_number_id = ICP.get_param("mudon_crm.wa_phone_number_id", "")
        if not (token and phone_number_id and to_digits):
            return (False, "", "Missing access token, phone-number id, or "
                    "recipient number.", True)
        try:
            import requests
        except ImportError:
            return (False, "", "Python 'requests' library not available.", True)
        url = "https://graph.facebook.com/%s/%s/messages" % (
            api_version, phone_number_id)
        payload = {
            "messaging_product": "whatsapp",
            "to": to_digits,
            "type": "text",
            "text": {"preview_url": True, "body": text or ""},
        }
        headers = {
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
        except Exception as exc:
            return (False, "", "Network error: %s" % exc, False)
        if resp.status_code // 100 == 2:
            try:
                wamid = (resp.json().get("messages") or [{}])[0].get("id", "")
            except Exception:
                wamid = ""
            return (True, wamid, "", False)
        # 4xx = permanent (bad number / unapproved template / auth);
        # 5xx + network = retryable
        permanent = resp.status_code // 100 == 4
        return (False, "", "HTTP %s: %s" % (resp.status_code, resp.text[:400]),
                permanent)

    def _mudon_send_whatsapp_meta(self, phone, body, from_company=False):
        """Send via Meta Cloud API + log the result. Keeps a chatter copy
        on success so the CRM card still shows what went out."""
        self.ensure_one()
        to_digits = (self._mudon_phone_normalize(phone) or "").lstrip("+")
        try:
            text = html2plaintext(body) if body else ""
        except Exception:
            text = str(body or "")
        ok, wamid, error, permanent = self._mudon_wa_meta_post(
            to_digits, text, from_company=from_company)
        status = "sent" if ok else ("failed_permanent" if permanent else "failed")
        self._mudon_wa_log(to_digits, body, status=status, wamid=wamid,
                           error=error, from_company=from_company)
        if ok:
            self.with_context(mudon_skip_first_contact=True).message_post(
                body=Markup("<p><b>[WhatsApp → %s]</b></p>%s")
                % (escape(to_digits or "(no number)"), body),
                subject=_("WhatsApp sent"))
        else:
            _logger.warning("mudon_crm: WA send failed for lead %s: %s",
                            self.id, error)
        return ok

    @api.model
    def _mudon_cron_wa_retry(self):
        """Re-send outbound WA messages that failed with a transient error
        (< 3 attempts). Permanent failures (bad number / unapproved
        template) are left as-is."""
        Msg = self.env["mudon.wa.message"].sudo()
        for msg in Msg.search([
            ("direction", "=", "out"),
            ("status", "=", "failed"),
            ("attempts", "<", 3),
        ], limit=100):
            lead = msg.lead_id
            if not lead:
                msg.status = "failed_permanent"
                continue
            to_digits = (lead._mudon_phone_normalize(msg.to_number)
                         or "").lstrip("+")
            ok, wamid, error, permanent = lead._mudon_wa_meta_post(
                to_digits, msg.body or "", from_company=msg.from_company)
            msg.attempts += 1
            if ok:
                msg.write({"status": "sent", "wamid": wamid or False,
                           "error": False})
            elif permanent or msg.attempts >= 3:
                msg.write({"status": "failed_permanent",
                           "error": error or msg.error})
            else:
                msg.write({"error": error or msg.error})

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
        # v19.0.1.2.0 — values now come from related records
        # (Many2one / Many2many) instead of Selection dicts.
        reason_label = (
            self.mudon_lost_reason_id.complete_name
            if self.mudon_lost_reason_id
            else "(no reason)"
        )
        city_name = self.mudon_city_id.name or ""
        source_name = self.mudon_source_id.name or ""
        detail_bits = [
            "Phone: %s" % (self.phone or ""),
            "Email: %s" % (self.email_from or ""),
            "City: %s" % city_name,
            "Source: %s" % source_name,
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
        """Dynamic stage-filter override:

        - If the current team has AT LEAST ONE stage bound to it via
          `team_ids`, the kanban shows ONLY those team-bound stages
          (the global Odoo defaults — New / Qualified / Proposition /
          Won — are filtered out).
        - If the team has no custom stages, standard behavior applies:
          pool of global stages + any team-bound ones.

        This is fully data-driven — no hardcoded team xmlid check —
        so any future Mudon pipeline (KSA, Qatar, a new region…) gets
        a clean kanban the moment its stages are seeded, with zero
        code changes. The semantic is "if you customised your stages,
        you own the column set."

        Cost: one indexed search_count per kanban load. Negligible.
        """
        team_id = self._context.get("default_team_id")
        if team_id:
            Stage = self.env["crm.stage"].sudo()
            has_custom_stages = Stage.search_count(
                [("team_ids", "=", team_id)], limit=1,
            )
            if has_custom_stages:
                return stages.search(
                    [("team_ids", "=", team_id)],
                    order=stages._order,
                )
        if hasattr(super(), "_read_group_stage_ids"):
            return super()._read_group_stage_ids(stages, domain)
        return stages.search(domain, order=stages._order)

    @api.model
    def _mudon_stage_ids(self, kind):
        """Return EVERY stage record across ALL Mudon teams whose
        `mudon_stage_kind` equals `kind`.

        Used by the SLA crons to filter leads by stage. Dynamic —
        a new Mudon pipeline added later (KSA, Qatar…) auto-rolls
        into the cron sweep without code changes, as long as its
        stages carry the right `mudon_stage_kind`.
        """
        stages = self.env["crm.stage"].sudo().search([
            ("mudon_stage_kind", "=", kind),
        ])
        return stages.ids

    # Stage-2 entry timestamp + SLA flag reset is handled inside
    # `_mudon_after_write` — keeping it out of `_track_subtype` avoids
    # writing tracked fields during Odoo's tracking pipeline, which
    # would re-enter our own write() and cascade.

    # ─── Manual "+ Mark New Offer Sent" button ──────────────────────
    def action_mudon_mark_offer_sent(self):
        """Agent clicks this each time they send a new offer to the
        client (videos/photos manually shared on WhatsApp). Bumps the
        counter by 1 (capped at 10), stamps `last_offer_date`, and
        resets the 3-hour reminder tracker so the next reminder fires
        for THIS offer.

        Equivalent to the agent sending an "Offer Sent" message via
        the META Cloud webhook — that path calls the same field
        writes, so behaviour is identical regardless of channel.
        """
        for rec in self:
            if rec.mudon_stage_kind_current != "offer_sent":
                from odoo.exceptions import UserError
                raise UserError(_(
                    "This action only applies on the Offer Sent stage."
                ))
            new_counter = min((rec.mudon_offer_counter or 0) + 1, 10)
            rec.sudo().with_context(mudon_in_write=True).write({
                "mudon_offer_counter": new_counter,
                "mudon_last_offer_date": fields.Datetime.now(),
                "mudon_offer_3hr_reminder_idx": (rec.mudon_offer_counter or 0),
            })
            rec.with_context(
                mudon_skip_first_contact=True,
            ).message_post(
                body=Markup(
                    "<p><b>Offer #%d marked sent.</b> "
                    "3-hour follow-up reminder queued.</p>"
                ) % new_counter,
            )
        return True

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
