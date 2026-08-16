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

# Meta Lead Ads built-in question names. Anything else a form asks is a
# custom question and is preserved in the lead notes.
_MUDON_META_STANDARD = {
    "full_name", "first_name", "last_name", "email", "phone_number",
    "city", "state", "province", "country", "zip", "post_code",
    "company_name", "job_title",
}

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
BEDS_SELECTION = [
    ("studio", "Studio"),
    ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"), ("5", "5"), ("6", "6"),
]
PROPERTY_QTY_SELECTION = [
    ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"), ("5", "5"), ("6", "6"),
]
MUDON_STAGE_FIELD_LABELS = {
    "mudon_service_id": "Service",
    "mudon_city_id": "City",
    "mudon_priority": "Priority",
    "expected_revenue": "Property Budget",
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
    # ONE sort key, but it means something different per stage — see
    # `_compute_mudon_sort_key`. Odoo's `_order` is model-global (a single
    # ORDER BY for every kanban column), while the client's spec asks for a
    # DIFFERENT sort on each stage. Encoding the per-stage rule into one
    # sortable string is what makes that possible, because a kanban column
    # only ever holds leads of a single stage kind.
    _order = "mudon_sort_key asc, id desc"

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
    # True when the lead sits on one of the 6 ordered funnel stages — used to
    # gate the touch stage-move buttons so they never render on a non-funnel
    # / blank stage (where advance/back would raise a bare UserError).
    mudon_is_funnel_stage = fields.Boolean(
        compute="_compute_mudon_is_funnel_stage", store=True,
        string="On a funnel stage",
    )

    @api.depends("stage_id.mudon_stage_kind")
    def _compute_mudon_is_funnel_stage(self):
        for rec in self:
            rec.mudon_is_funnel_stage = (
                rec.stage_id.mudon_stage_kind in MUDON_STAGE_ORDER)

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
        PRIORITY_SELECTION, string="Priority",
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
    # Relabel of the stock CRM field, per field change 4: "Rename Expected
    # Revenue to Property Budget". Done here rather than per view so the
    # kanban, list, search bar and every export agree on one name.
    expected_revenue = fields.Monetary(string="Property Budget")
    mudon_status_id = fields.Many2one(
        "mudon.lead.status", string="Call Status", ondelete="restrict",
    )
    mudon_nationality_id = fields.Many2one("res.country", string="Nationality")
    mudon_living_in_id = fields.Many2one(
        "res.country", string="Living Country")
    mudon_living_city_id = fields.Many2one(
        "mudon.living.city", string="Living City", ondelete="restrict",
        help="Where the client currently lives. Managed under "
             "Configuration → Living Cities.",
    )
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
    mudon_property_area_ids = fields.Many2many(
        "mudon.property.area", "crm_lead_mudon_property_area_rel",
        "lead_id", "area_id",
        string="Property Area",
    )
    mudon_property_qty = fields.Selection(
        PROPERTY_QTY_SELECTION, string="Property Qty",
    )
    # Was an Integer. The client asked for the button row in the
    # screenshot, and "Studio" is not a number, so it had to become a
    # Selection. The 19.0.1.16.0 migration carries the old counts over.
    mudon_beds = fields.Selection(BEDS_SELECTION, string="No. of Beds")
    mudon_other_specs = fields.Text(string="Other Specifications")
    mudon_visit_date = fields.Date(string="Expected Visit Date")
    mudon_notes = fields.Text(string="Notes")
    mudon_source_id = fields.Many2one(
        "mudon.source", string="Source", ondelete="restrict",
    )
    # Kept in the database but off every screen — field change 3 asked for
    # it removed, and dropping the column would lose what Turkey has
    # already captured.
    mudon_cbi_files = fields.Integer(string="No. of CBI Files")

    # Affordability, captured at enquiry (field change 13). Both amounts
    # follow the pipeline currency, so Dubai reads AED and Turkey USD.
    mudon_full_amount_ready = fields.Selection(
        YESNO_SELECTION, string="Full Amount Ready",
    )
    mudon_ready_amount = fields.Monetary(
        string="Ready Amount", currency_field="mudon_budget_currency_id",
    )
    mudon_monthly_amount = fields.Monetary(
        string="Available Monthly Amount",
        currency_field="mudon_budget_currency_id",
    )

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
    # The client now enters a percentage of the Closing Amount rather than
    # a figure. The amount is kept as a stored compute, not dropped, because
    # both dashboards, the XLSX export and the deal register all read
    # `mudon_commission` — changing what they read would be a far wider
    # change than the client asked for.
    mudon_commission_pct = fields.Float(
        string="Commission %", digits=(5, 2),
        help="Percentage of the Closing Amount.",
    )
    mudon_commission = fields.Monetary(
        string="Commission", currency_field="mudon_budget_currency_id",
        compute="_compute_mudon_commission", store=True, readonly=False,
        tracking=True,
        help="Worked out from the percentage and the Closing Amount, but you "
             "can also type it in directly. Changing either of those two "
             "recalculates it.",
    )
    mudon_commission_type_id = fields.Many2one(
        "mudon.commission.type", string="Commission Type",
        ondelete="restrict",
    )
    # Billing / collection tracking (CRM-native) — filled by admin as
    # invoices go out and money comes in. Powers the Financial dashboard's
    # Won / Invoiced / Collected / Unbilled figures without needing the
    # Accounting module. (An "Accounting" source on the dashboard can pull
    # real account.move data instead.)
    mudon_invoiced_amount = fields.Monetary(
        string="Invoiced (commission billed)",
        currency_field="mudon_budget_currency_id",
        help="Commission amount invoiced to date for this deal.",
    )
    mudon_invoiced_date = fields.Date(string="Invoice Date")
    mudon_collected_amount = fields.Monetary(
        string="Collected (cash in)",
        currency_field="mudon_budget_currency_id",
        help="Commission amount actually collected for this deal.",
    )
    mudon_collected_date = fields.Date(string="Payment Received Date")
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
    # Stat-button counters (non-stored — cheap, always fresh).
    mudon_admin_task_count = fields.Integer(
        compute="_compute_mudon_task_counts", string="Admin Task Count",
    )
    mudon_after_sales_task_count = fields.Integer(
        compute="_compute_mudon_task_counts", string="After-Sales Task Count",
    )

    @api.depends("mudon_admin_task_ids", "mudon_after_sales_task_ids")
    def _compute_mudon_task_counts(self):
        for rec in self:
            rec.mudon_admin_task_count = len(rec.mudon_admin_task_ids)
            rec.mudon_after_sales_task_count = len(rec.mudon_after_sales_task_ids)

    # Digits-only phone for the one-tap WhatsApp link on the kanban card.
    # Computed server-side because the QWeb kanban expression compiler can't
    # handle a JS regex literal (it mistakes the /.../g flag for a variable).
    mudon_wa_phone = fields.Char(
        compute="_compute_mudon_wa_phone", string="WhatsApp digits",
    )

    @api.depends("phone")
    def _compute_mudon_wa_phone(self):
        for rec in self:
            raw = rec.phone or ""
            rec.mudon_wa_phone = "".join(ch for ch in raw if ch.isdigit())

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

    @api.depends("user_id", "type")
    def _compute_team_id(self):
        """Keep the pipeline (Sales Team) STICKY across salesperson changes.

        Odoo core computes ``team_id`` from the salesperson
        (team-follows-user). Because Mudon binds every stage to a specific
        team via ``crm.stage.team_ids``, that stock behaviour rips a lead
        out of its Turkey/UAE pipeline the moment its agent is reassigned:
        ``team_id`` detaches, then ``stage_id`` and ``mudon_pipeline_kind``
        (both team-derived) reset — so the statusbar, the kanban columns
        AND the whole custom card (gated on ``mudon_pipeline_kind``) vanish.

        A lead's pipeline is a routing decision taken at creation, not a
        side-effect of who works it. So we let core assign a team only when
        none is set yet (new leads / imports / country-code routing); an
        existing team is left untouched on a ``user_id`` change. A manager
        can still deliberately move a lead to another pipeline by editing
        the Sales Team field directly.
        """
        without_team = self.filtered(lambda lead: not lead.team_id)
        if without_team:
            super(CrmLead, without_team)._compute_team_id()
        # Leads already in a pipeline keep their team → no stage_id /
        # mudon_pipeline_kind cascade, the board stays intact.

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

    @api.depends("mudon_closing_amount", "mudon_commission_pct")
    def _compute_mudon_commission(self):
        for rec in self:
            rec.mudon_commission = (
                (rec.mudon_closing_amount or 0.0)
                * (rec.mudon_commission_pct or 0.0) / 100.0
            )

    # ─── Column total in the pipeline's own currency (comment 23) ──────
    # The kanban column header sums `expected_revenue`, whose currency_field
    # is the COMPANY currency, so the Dubai board totalled AED amounts and
    # printed them as USD. This mirror carries the same number against the
    # pipeline currency, and the kanban progressbar sums this instead.
    mudon_pipeline_revenue = fields.Monetary(
        string="Budget (pipeline currency)",
        currency_field="mudon_budget_currency_id",
        compute="_compute_mudon_pipeline_revenue",
        store=True, readonly=True,
    )

    @api.depends("expected_revenue")
    def _compute_mudon_pipeline_revenue(self):
        for rec in self:
            rec.mudon_pipeline_revenue = rec.expected_revenue or 0.0

    # ─── Yes/No mirrors so tick-boxes are searchable (comment 22) ──────
    # "we may need to replace all these green tick box with yes/no to make it
    # searchable" - correct diagnosis: Odoo's search bar cannot offer a
    # Boolean for free-text search, so those fields never appeared in the
    # type-ahead list. Rather than change the stored type (which every stage
    # gate and automation reads), each tick gets a stored Selection mirror.
    # The Boolean stays the source of truth; the mirror exists purely so the
    # field is searchable and groupable as Yes / No.
    MUDON_YESNO_MIRRORS = {
        "mudon_in_country_yn": "mudon_in_country",
        "mudon_tick_offer_sent_yn": "mudon_tick_offer_sent",
        "mudon_visit_confirmed_yn": "mudon_visit_confirmed",
        "mudon_paid_booking_yn": "mudon_paid_booking",
        "mudon_fully_paid_yn": "mudon_fully_paid",
        "mudon_need_invoice_yn": "mudon_need_invoice",
        "mudon_title_deed_required_yn": "mudon_title_deed_required",
        "mudon_citizenship_required_yn": "mudon_citizenship_required",
        "mudon_residence_required_yn": "mudon_residence_required",
        "mudon_furniture_required_yn": "mudon_furniture_required",
    }

    mudon_in_country_yn = fields.Selection(
        YESNO_SELECTION, string="In Country Now (Yes/No)",
        compute="_compute_mudon_yesno", store=True, readonly=True)
    mudon_tick_offer_sent_yn = fields.Selection(
        YESNO_SELECTION, string="Offer Sent (Yes/No)",
        compute="_compute_mudon_yesno", store=True, readonly=True)
    mudon_visit_confirmed_yn = fields.Selection(
        YESNO_SELECTION, string="Visit Confirmed (Yes/No)",
        compute="_compute_mudon_yesno", store=True, readonly=True)
    mudon_paid_booking_yn = fields.Selection(
        YESNO_SELECTION, string="Paid Booking (Yes/No)",
        compute="_compute_mudon_yesno", store=True, readonly=True)
    mudon_fully_paid_yn = fields.Selection(
        YESNO_SELECTION, string="Fully Paid (Yes/No)",
        compute="_compute_mudon_yesno", store=True, readonly=True)
    mudon_need_invoice_yn = fields.Selection(
        YESNO_SELECTION, string="Need Invoice (Yes/No)",
        compute="_compute_mudon_yesno", store=True, readonly=True)
    mudon_title_deed_required_yn = fields.Selection(
        YESNO_SELECTION, string="Title Deed Required (Yes/No)",
        compute="_compute_mudon_yesno", store=True, readonly=True)
    mudon_citizenship_required_yn = fields.Selection(
        YESNO_SELECTION, string="Citizenship Required (Yes/No)",
        compute="_compute_mudon_yesno", store=True, readonly=True)
    mudon_residence_required_yn = fields.Selection(
        YESNO_SELECTION, string="Residence Required (Yes/No)",
        compute="_compute_mudon_yesno", store=True, readonly=True)
    mudon_furniture_required_yn = fields.Selection(
        YESNO_SELECTION, string="Furniture / Other Required (Yes/No)",
        compute="_compute_mudon_yesno", store=True, readonly=True)

    @api.depends("mudon_in_country", "mudon_tick_offer_sent",
                 "mudon_visit_confirmed", "mudon_paid_booking",
                 "mudon_fully_paid", "mudon_need_invoice",
                 "mudon_title_deed_required", "mudon_citizenship_required",
                 "mudon_residence_required", "mudon_furniture_required")
    def _compute_mudon_yesno(self):
        for rec in self:
            for mirror, source in self.MUDON_YESNO_MIRRORS.items():
                rec[mirror] = "yes" if rec[source] else "no"

    # ─── Auto country code on the phone number ──────────────────────────
    # Client: "if in turkey pipeline i put number it should auto add turkey
    # country code same for dubai". A number typed without an international
    # prefix is assumed to belong to the pipeline's own country, which also
    # keeps the country-code routing and the wa.me links working.
    MUDON_PIPELINE_DIAL_CODE = {"turkey": "+90", "uae": "+971"}

    @api.onchange("phone", "mudon_pipeline_kind")
    def _onchange_mudon_phone_dial_code(self):
        for rec in self:
            rec.phone = rec._mudon_apply_dial_code(rec.phone)

    def _mudon_apply_dial_code(self, phone):
        """Prefix a local number with the pipeline's dial code.

        Left alone when the number already carries any international prefix
        (leading + or 00), when it already starts with this pipeline's code,
        or when there is no pipeline. A local number written with a national
        trunk zero (0532...) drops that zero, which is what the international
        format requires.
        """
        self.ensure_one()
        raw = (phone or "").strip()
        if not raw:
            return phone
        code = self.MUDON_PIPELINE_DIAL_CODE.get(self.mudon_pipeline_kind)
        if not code:
            return phone
        compact = re.sub(r"[^\d+]", "", raw)
        if compact.startswith("+") or compact.startswith("00"):
            return phone
        digits = compact.lstrip("0")
        if not digits:
            return phone
        if digits.startswith(code.lstrip("+")):
            return "+" + digits
        return "%s %s" % (code, digits)

    # ─── Per-stage kanban sort (client comments 18 + 27) ────────────────
    mudon_sort_key = fields.Char(
        compute="_compute_mudon_sort_key",
        store=True, index=True, readonly=True,
        string="Kanban sort key",
        help="Internal. Encodes the sort rule the client specified for the "
             "lead's CURRENT stage into one sortable string, because Odoo "
             "applies a single _order to every kanban column.",
    )

    @api.depends("mudon_stage_kind_current", "mudon_kanban_priority_rank",
                 "mudon_visit_date", "create_date")
    def _compute_mudon_sort_key(self):
        """Build a fixed-width, ascending-sortable key per the spec:

        =============  ==========================================
        Stage          Order requested by the client
        =============  ==========================================
        New Lead       Most recent on top                (#27)
        Qualified      P1 → P4, latest first within each
        Offer Sent     P1 → P4, then nearest visit date
        Meeting        Nearest expected visit date on top (#18)
        EOI/WON/Lost   P1 → P4, latest first within each
        =============  ==========================================

        The key is ``RDDDDDDDRRRRRRRRRR`` — 1 digit priority band, 7 digits
        visit-date, 10 digits inverted recency. A component set to zero
        simply drops out of the comparison, which is how one column sorts
        purely by date and another purely by recency.
        """
        # Sentinels chosen so "unset" always sorts LAST, never first.
        epoch_max = 4102444800          # 2100-01-01, > any create_date
        no_date = 9999999               # > any real date.toordinal()
        for rec in self:
            kind = rec.mudon_stage_kind_current

            created = rec.create_date
            recency = epoch_max
            if created:
                # Inverted so the NEWEST record yields the SMALLEST number.
                recency = max(0, epoch_max - int(created.timestamp()))

            visit = rec.mudon_visit_date
            # Ascending ordinal => the NEAREST date sorts first; blanks last.
            date_part = visit.toordinal() if visit else no_date

            rank = rec.mudon_kanban_priority_rank or 5

            if kind == "new_lead":
                band, dpart = 0, 0                    # pure recency
            elif kind == "qualified":
                band, dpart = rank, 0                 # priority, then recency
            elif kind == "offer_sent":
                band, dpart = rank, date_part         # priority, then visit
            elif kind == "meeting":
                band, dpart = 0, date_part            # pure visit date
            else:
                # EOI / WON / Lost / non-Mudon: priority then recency.
                band, dpart = rank, 0

            rec.mudon_sort_key = "%1d%07d%010d" % (band, dpart, recency)

    # ─── Create / write: stage flow + funnel spawn ──────────────────
    # Odoo auto-titles an opportunity created from a contact as
    # "<Contact>'s opportunity". The client asked for that wording gone
    # from every card (comment 13), and it lives in the stored `name`, not
    # in the view — so it has to be stripped on the way in.
    _MUDON_NAME_SUFFIX = "'s opportunity"

    @api.model
    def _mudon_clean_name(self, name):
        """Drop Odoo's auto-appended "'s opportunity" tail from a title."""
        if not name:
            return name
        for suffix in (self._MUDON_NAME_SUFFIX, "’s opportunity"):
            if name.endswith(suffix):
                return name[: -len(suffix)].strip() or name
        return name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name"):
                vals["name"] = self._mudon_clean_name(vals["name"])
            # The dial-code onchange only runs in the UI. Imported rows come
            # straight through create(), so apply it here as well.
            if self.env.context.get("mudon_import_mode") and vals.get("phone"):
                code = self.MUDON_PIPELINE_DIAL_CODE.get(
                    self.env.context.get("mudon_import_pipeline"))
                raw = re.sub(r"[^\d+]", "", str(vals["phone"]).strip())
                if code and raw and not raw.startswith(("+", "00")):
                    digits = raw.lstrip("0")
                    if digits and not digits.startswith(code.lstrip("+")):
                        vals["phone"] = "%s %s" % (code, digits)
                    elif digits:
                        vals["phone"] = "+" + digits
        leads = super().create(vals_list)
        for lead, vals in zip(leads, vals_list):
            try:
                # Was a salesperson DELIBERATELY chosen? Odoo defaults
                # `user_id` to whoever is creating the record, so "the field
                # is filled" is NOT evidence of an explicit choice — and the
                # old code treated it as such, which is why country-code
                # routing and round-robin never ran for UI-created leads
                # (comments 14 / 15 / 16). Only a salesperson OTHER than the
                # creator counts as a deliberate assignment.
                chosen = vals.get("user_id")
                explicit = bool(chosen) and chosen != self.env.uid
                lead._mudon_auto_assign_agent(force=not explicit)
                # A bulk import must still be ROUTED, but it must not greet
                # every client, page every agent, or push leads off New Lead.
                # Importing 500 rows would otherwise fire 1000 WhatsApp
                # messages and empty the New Lead column.
                if not self.env.context.get("mudon_import_mode"):
                    lead._mudon_send_client_greeting()
                    lead._mudon_notify_assigned_agent("new_lead")
                    # A lead captured with all four qualifying fields already
                    # filled belongs on Qualified, not New Lead (comment 17).
                    lead._mudon_try_auto_qualify()
            except Exception as exc:
                _logger.warning(
                    "mudon_crm: post-create hook failed for lead %s: %s",
                    lead.id, exc,
                )
        return leads

    def _mudon_try_auto_qualify(self):
        """Advance New Lead → Qualified once the intake data is complete.

        Client comment 17: "If agent opens a card and fills the required
        fields, it should advance the card automatically." The four fields
        are exactly the gate the funnel already enforces, so satisfying the
        gate is now enough to cross it — no drag needed.
        """
        self.ensure_one()
        if not self.mudon_pipeline_kind:
            return False
        if self.env.context.get("mudon_import_mode"):
            return False
        if self.mudon_stage_kind_current != "new_lead":
            return False
        if any(not self[f] for f in MUDON_QUALIFY_DATA):
            return False
        self._mudon_advance_stage("qualified")
        return True

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

        Abdul Rehman 07-06 feedback (items 6 + 9): EVERY forward move
        must ask for confirmation of the milestone tick — no silent
        auto-tick on adjacent drags. So single-step and multi-step
        advances are treated identically: every unset crossed field
        opens the wizard.

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
            # Confirm EVERY crossed field, single-step or skip. The
            # wizard collects them all together.
            required = [
                f for f in crossed if not (rec[f] or vals.get(f))
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
    # Kept for reference / potential future use. As of Abdul Rehman
    # 07-06 feedback the auto-tick behaviour is removed — every gated
    # forward move goes through the quick-fill wizard.
    MUDON_AUTOTICK_GATES = {
        "offer_sent": "mudon_tick_offer_sent",
        "meeting": "mudon_visit_confirmed",
        "eoi": "mudon_paid_booking",
        "won": "mudon_fully_paid",
    }

    def _mudon_check_stage_transitions(self, new_stage, vals=None):
        """Enforce Lost has a Lost Reason before the write goes through.

        Offer Sent / Meeting / EOI / WON are covered by
        _mudon_check_stage_requirements (crossed-field gate). Lost is
        singled out here because the reason can't be inferred from
        other data.

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

    # ─── Moving a card BACK (client comment 3) ──────────────────────
    # Which stage OWNS which fields. Moving back past a stage clears
    # everything that stage collected, so a card never carries data — or a
    # milestone tick — belonging to a stage it is no longer on. (Leaving
    # the ticks in place was the actual complaint: the tick that advanced
    # the card was still set, so the card sprang forward again.)
    MUDON_STAGE_OWNED_FIELDS = {
        "qualified": (
            ("mudon_tick_offer_sent", "Offer Sent tick"),
        ),
        "offer_sent": (
            ("mudon_offer_counter", "Offer counter"),
            ("mudon_last_offer_date", "Last offer sent at"),
            ("mudon_seriousness", "Seriousness"),
            ("mudon_visit_confirmed", "Visit Confirmed tick"),
            ("mudon_3rd_offer_survey_sent", "3rd-offer survey status"),
            ("mudon_survey_received_offer", "Survey: received offer"),
            ("mudon_survey_suitable", "Survey: was it suitable"),
            ("mudon_survey_needs_support", "Survey: needs support"),
            ("mudon_survey_property_options", "Survey: property options"),
            ("mudon_survey_status", "Survey: status"),
            ("mudon_survey_preferred_support", "Survey: preferred support"),
            ("mudon_offer_3hr_reminder_idx", None),
            ("mudon_last_15day_reminder_date", None),
        ),
        "meeting": (
            ("mudon_client_type", "Client Type"),
            ("mudon_property_requirements", "Property Requirements"),
            ("mudon_paid_booking", "Paid Booking tick"),
            ("mudon_meeting_reminder_3day_sent", None),
            ("mudon_meeting_reminder_2day_sent", None),
            ("mudon_meeting_reminder_1day_sent", None),
        ),
        "eoi": (
            ("mudon_fully_paid", "Fully Paid tick"),
        ),
        "won": (
            ("mudon_closing_amount", "Closing Amount"),
            ("mudon_developer_id", "Developer"),
            ("mudon_project_id", "Project"),
            ("mudon_commission", "Commission"),
            ("mudon_handover_type", "Handover Type"),
            ("mudon_reason_to_win", "Reason to Win"),
            ("mudon_invoiced_amount", "Invoiced amount"),
            ("mudon_invoiced_date", "Invoice date"),
            ("mudon_collected_amount", "Collected amount"),
            ("mudon_collected_date", "Payment received date"),
            ("mudon_need_invoice", "Need Invoice flag"),
            ("mudon_title_deed_required", "Title Deed flag"),
            ("mudon_citizenship_required", "Citizenship flag"),
            ("mudon_residence_required", "Residence flag"),
            ("mudon_furniture_required", "Furniture flag"),
        ),
    }

    def _mudon_fields_cleared_on_back(self, target_kind, labels_only=False):
        """Fields (or human labels) belonging to stages after `target_kind`.

        Only fields that actually hold a value are reported, so the
        confirmation dialog lists what will really be lost on THIS card
        rather than a generic catalogue.
        """
        self.ensure_one()
        order = MUDON_STAGE_ORDER
        if target_kind not in order:
            return []
        ti = order.index(target_kind)
        out = []
        for kind in order[ti + 1:]:
            for fname, label in self.MUDON_STAGE_OWNED_FIELDS.get(kind, ()):
                if labels_only:
                    if label and self[fname]:
                        out.append(label)
                else:
                    out.append(fname)

        # The tick that advanced the card OUT of the stage we are landing
        # on must go too. It is registered under that stage, not under the
        # ones being abandoned, so the loop above never reaches it — which
        # is why a card dragged from Meeting back to Offer Sent kept Visit
        # Confirmed ticked, and Offer Sent back to Qualified kept the Offer
        # Sent tick. Left set, the card either re-advances immediately or
        # claims a milestone it no longer has.
        nxt = order[ti + 1] if ti + 1 < len(order) else None
        gate = MUDON_STAGE_REQUIRED.get(nxt, ()) if nxt else ()
        # New Lead's gate is the intake data (service / city / priority /
        # budget), which is the lead's own information rather than a
        # milestone. Moving a card back must never wipe that.
        if gate and gate != MUDON_QUALIFY_DATA:
            labels_here = dict(
                self.MUDON_STAGE_OWNED_FIELDS.get(target_kind, ()))
            for fname in gate:
                if labels_only:
                    label = labels_here.get(fname)
                    if label and self[fname]:
                        out.append(label)
                elif fname not in out:
                    out.append(fname)
        # Reopening a Lost card must also drop the reason that closed it.
        if self.mudon_stage_kind_current == "lost":
            if labels_only:
                if self.mudon_lost_reason_id:
                    out.append("Lost Reason")
            else:
                out.append("mudon_lost_reason_id")
        return out

    @api.model
    def _mudon_cron_archive_lost(self):
        """Archive long-dead Lost cards (client comment 16).

        Archiving, never deleting: the lead stays in the database and in
        reporting, it just stops filling the Lost column. Off unless the
        client sets a number of days in Settings.
        """
        days = int(self.env["ir.config_parameter"].sudo().get_param(
            "mudon_crm.lost_archive_days", 0) or 0)
        if days <= 0:
            return 0
        cutoff = fields.Datetime.subtract(
            fields.Datetime.now(), days=days)
        leads = self.sudo().search([
            ("mudon_stage_kind_current", "=", "lost"),
            ("active", "=", True),
            ("write_date", "<", cutoff),
        ])
        if leads:
            leads.write({"active": False})
            _logger.info(
                "mudon_crm: archived %s lost lead(s) untouched for %s days",
                len(leads), days)
        return len(leads)

    def _mudon_check_move_back(self, new_stage):
        """Ask before a backward move, per the client's requested dialog."""
        if self.env.context.get("mudon_confirm_back"):
            return
        if not new_stage or not new_stage.mudon_stage_kind:
            return
        target_kind = new_stage.mudon_stage_kind
        if target_kind not in MUDON_STAGE_ORDER:
            return          # moving to Lost is handled by its own gate
        from odoo.exceptions import RedirectWarning
        ti = MUDON_STAGE_ORDER.index(target_kind)
        for rec in self:
            if not rec.mudon_pipeline_kind:
                continue
            ck = rec.mudon_stage_kind_current
            is_back = (ck == "lost") or (
                ck in MUDON_STAGE_ORDER
                and MUDON_STAGE_ORDER.index(ck) > ti
            )
            if not is_back:
                continue
            action = self.env["ir.actions.act_window"]._for_xml_id(
                "mudon_crm.action_mudon_move_back_wizard",
            )
            action["context"] = {
                "default_lead_id": rec.id,
                "default_target_stage_id": new_stage.id,
            }
            raise RedirectWarning(
                _("Move Card Back? This will delete all entries from later "
                  "stages for %s.") % (
                    rec.name or rec.contact_name or _("this lead"),
                ),
                action,
                _("Move Back"),
            )

    def _mudon_move_back_to(self, stage):
        """Clear the abandoned stages' data, then land on `stage`."""
        self.ensure_one()
        vals = {}
        for fname in self._mudon_fields_cleared_on_back(
                stage.mudon_stage_kind):
            vals[fname] = False
        # Integer fields must go to 0, not False, to stay type-correct.
        for int_field in ("mudon_offer_counter", "mudon_offer_3hr_reminder_idx"):
            if int_field in vals:
                vals[int_field] = 0
        vals["stage_id"] = stage.id
        return self.sudo().with_context(
            mudon_in_write=True, mudon_confirm_back=True,
        ).write(vals)

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
            self._mudon_check_move_back(new_stage)
            self._mudon_check_stage_requirements(new_stage, vals)
            self._mudon_check_stage_transitions(new_stage, vals)
            # NOTE: no silent auto-tick — per Abdul 07-06 feedback,
            # every forward move opens the quick-fill wizard so the
            # user consciously confirms the milestone. The wizard
            # writes the tick + stage_id together in a single vals.
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

        # New Lead → Qualified as soon as the intake data is complete
        # (comment 17). Runs before the tick-driven transitions below so a
        # single save that both completes intake AND ticks Offer Sent still
        # lands on the further stage.
        if "stage_id" not in vals:
            self._mudon_try_auto_qualify()

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

    # ─── Touch stage-move (kanban buttons — mobile can't drag) ──────────
    def _mudon_stage_of_kind(self, kind):
        """The stage record of the given kind on THIS lead's team."""
        self.ensure_one()
        if not self.team_id:
            return self.env["crm.stage"]
        return self.env["crm.stage"].sudo().search([
            ("team_ids", "=", self.team_id.id),
            ("mudon_stage_kind", "=", kind),
        ], limit=1)

    def action_mudon_kanban_advance(self):
        """One-step forward move for a kanban card on touch devices.

        Writes the next funnel stage via the PUBLIC path, so it goes through
        the exact same gate + quick-fill-wizard flow as a drag — just tappable.
        """
        self.ensure_one()
        from odoo.exceptions import UserError
        order = MUDON_STAGE_ORDER
        ck = self.stage_id.mudon_stage_kind
        if ck not in order:
            raise UserError(_("This lead is not on a Mudon funnel stage."))
        i = order.index(ck)
        if i >= len(order) - 1:
            raise UserError(_("“%s” is already at the final stage (WON).")
                            % (self.name or _("This lead")))
        stage = self._mudon_stage_of_kind(order[i + 1])
        if not stage:
            raise UserError(
                _("No “%s” stage is configured for this pipeline.")
                % order[i + 1])
        self.write({"stage_id": stage.id})
        return True

    def action_mudon_kanban_back(self):
        """One-step backward move for a kanban card (no gate on retreat).

        From Lost it reopens the lead at the first funnel stage (mistaken-Lost
        recovery from a phone, where there is no drag).
        """
        self.ensure_one()
        from odoo.exceptions import UserError
        order = MUDON_STAGE_ORDER
        ck = self.stage_id.mudon_stage_kind
        if ck == "lost":
            stage = self._mudon_stage_of_kind("new_lead")
            if not stage:
                raise UserError(
                    _("No “New Lead” stage is configured for this pipeline."))
            self.write({"stage_id": stage.id})
            return True
        if ck not in order:
            raise UserError(_("This lead is not on a Mudon funnel stage."))
        i = order.index(ck)
        if i <= 0:
            raise UserError(_("“%s” is already at the first stage.")
                            % (self.name or _("This lead")))
        stage = self._mudon_stage_of_kind(order[i - 1])
        if not stage:
            raise UserError(
                _("No “%s” stage is configured for this pipeline.")
                % order[i - 1])
        self.write({"stage_id": stage.id})
        return True

    # ─── Header actions: Mark Lost / Restore / open task lists ──────
    def action_mudon_mark_lost(self):
        """Header **Mark Lost** — route to THIS pipeline's Lost stage via the
        public write path so the lost-reason gate fires: a lead with no
        reason set opens the quick-fill wizard (asks for the reason, then
        writes the Lost stage), one that already has a reason goes straight
        to Lost. Identical behaviour to dragging the card to Lost — no
        bypass, so the mandatory-reason data rule holds everywhere."""
        self.ensure_one()
        from odoo.exceptions import UserError
        stage = self._mudon_stage_of_kind("lost")
        if not stage:
            raise UserError(
                _("No Lost stage is configured for this pipeline."))
        return self.write({"stage_id": stage.id})

    def action_mudon_reopen(self):
        """Header **Restore** — bring a Lost lead back to New Lead
        (reuses the kanban back-move, which special-cases lost→new_lead)."""
        return self.action_mudon_kanban_back()

    def action_mudon_open_admin_tasks(self):
        """Stat button → the Admin Funnel tasks spawned by this deal."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Admin Funnel Tasks"),
            "res_model": "mudon.admin.task",
            "domain": [("id", "in", self.mudon_admin_task_ids.ids)],
            "views": [[False, "list"], [False, "form"]],
            "target": "current",
            "context": {"default_lead_id": self.id},
        }

    def action_mudon_open_after_sales_tasks(self):
        """Stat button → the After-Sales Funnel tasks for this deal."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("After-Sales Tasks"),
            "res_model": "mudon.after.sales.task",
            "domain": [("id", "in", self.mudon_after_sales_task_ids.ids)],
            "views": [[False, "list"], [False, "form"]],
            "target": "current",
            "context": {"default_lead_id": self.id},
        }

    # ─── Header shortcuts (Abdul 07-06 UX feedback) ─────────────────
    # Item 7: a discoverable Delete button; item 8: an explicit Back
    # to Pipeline so exiting a card doesn't feel like navigation
    # guesswork. Both return an Odoo action that navigates cleanly.
    def _mudon_pipeline_action(self):
        """The pipeline act_window scoped to this lead's team, so the user
        lands back exactly where they came from (Turkey / UAE).

        Client comment 12 — "Back to pipeline is not working" — was this
        method crashing to the generic "Oops! Something went wrong" screen.
        ``_for_xml_id`` returns ``context`` as the RAW STRING stored on the
        action (``"{'default_type': 'opportunity'}"``), and ``dict()`` of a
        string raises ValueError. Parse it properly instead, and prefer the
        Mudon pipeline action so the user returns to the branded board with
        its stage columns rather than the generic CRM pipeline.
        """
        self.ensure_one()
        xmlid_by_kind = {
            "turkey": "mudon_crm.mudon_pipeline_turkey_action",
            "uae": "mudon_crm.mudon_pipeline_uae_action",
        }
        xmlid = xmlid_by_kind.get(
            self.mudon_pipeline_kind, "crm.crm_lead_action_pipeline")
        try:
            action = self.env["ir.actions.act_window"]._for_xml_id(xmlid)
        except ValueError:
            action = self.env["ir.actions.act_window"]._for_xml_id(
                "crm.crm_lead_action_pipeline")

        raw_ctx = action.get("context") or {}
        if isinstance(raw_ctx, str):
            from odoo.tools.safe_eval import safe_eval
            try:
                raw_ctx = safe_eval(raw_ctx, {"uid": self.env.uid}) or {}
            except Exception:
                raw_ctx = {}
        ctx = dict(raw_ctx)
        if self.team_id:
            ctx["default_team_id"] = self.team_id.id
        action["context"] = ctx
        # Land on the kanban board, not back on a form.
        action["target"] = "main"
        action["res_id"] = False
        return action

    def action_mudon_back_to_pipeline(self):
        """Header **← Back to Pipeline** — close the form cleanly and
        return the user to the kanban of the same team. No side-effects."""
        self.ensure_one()
        return self._mudon_pipeline_action()

    def action_mudon_delete_lead(self):
        """Header **Delete** — remove this lead + return to the pipeline.

        ACL: gated to Sales Managers via ``groups=`` in the view. If a
        salesperson somehow reaches this method (context bypass), the
        ORM's own security check on ``unlink`` will reject the delete.
        """
        self.ensure_one()
        action = self._mudon_pipeline_action()
        self.unlink()
        return action

    # ─── Meta Lead Ads intake ───────────────────────────────────────────
    mudon_meta_leadgen_id = fields.Char(
        string="Meta lead ID", index=True, copy=False, readonly=True,
        help="The Meta Lead Ads submission this lead came from. Present "
             "only on leads captured from a Facebook or Instagram form.",
    )

    @api.model
    def _mudon_create_from_meta(self, answers, mapping, leadgen_id):
        """Create a New Lead from one Meta instant-form submission.

        `answers` is the flattened form: {"full_name": "...", "email": ...}.
        Meta's built-in questions map onto real fields; every custom
        question is written verbatim into the notes, so an answer is never
        lost just because the form asked something we did not anticipate.
        """
        stage = self.env["crm.stage"].sudo().search([
            ("team_ids", "=", mapping.team_id.id),
            ("mudon_stage_kind", "=", "new_lead"),
        ], limit=1)

        name = (answers.get("full_name")
                or " ".join(x for x in (answers.get("first_name"),
                                        answers.get("last_name")) if x).strip()
                or answers.get("email")
                or _("Meta lead %s") % leadgen_id)

        vals = {
            "name": name,
            "contact_name": name,
            "type": "opportunity",
            "team_id": mapping.team_id.id,
            "mudon_meta_leadgen_id": leadgen_id,
        }
        if stage:
            vals["stage_id"] = stage.id
        if answers.get("email"):
            vals["email_from"] = answers["email"]
        if answers.get("phone_number"):
            vals["phone"] = answers["phone_number"]
        if mapping.source_id:
            vals["mudon_source_id"] = mapping.source_id.id

        # Everything the form asked that is not a built-in question.
        extras = ["%s: %s" % (k.replace("_", " ").title(), v)
                  for k, v in answers.items()
                  if k not in _MUDON_META_STANDARD and v]
        if extras:
            vals["mudon_other_specs"] = "\n".join(extras)

        # Import mode: route the lead to an agent, but do not fire the
        # client greeting or push it off New Lead. A Meta lead has not
        # consented to WhatsApp yet, and the requirement is New Lead.
        return self.with_context(
            mudon_import_mode=True,
            mudon_import_pipeline=mapping.team_id == self.env.ref(
                "mudon_crm.mudon_team_turkey", raise_if_not_found=False)
            and "turkey" or "uae",
        ).create(vals)

    # ─── Branch + country-code routing ──────────────────────────────
    def _mudon_auto_assign_agent(self, force=False):
        """Route the lead to an agent.

        ``force=True`` overwrites the salesperson Odoo defaulted to (the
        creating user); see the note in ``create``. With ``force=False``
        an existing assignment is respected.
        """
        self.ensure_one()
        if not self.mudon_pipeline_kind:
            return
        if self.user_id and not force:
            return
        agent = self._mudon_route_agent()
        if agent and agent.id != self.user_id.id:
            self.sudo().with_context(mudon_in_write=True).write(
                {"user_id": agent.id})

    def _mudon_route_agent(self):
        """Pick the agent per the client's routing rules.

        Comment 16 clarified the intent: *"Round robin will distribute only
        to agent in the same city"*, and cities with no branch go to the
        Sales Manager. The old chain fell through from an empty branch to a
        team-wide round-robin, which leaked leads to agents in other
        cities — so the city branch is now a hard boundary:

        1. City maps to a branch  → round-robin INSIDE that branch only;
           an empty branch falls to the Sales Manager, never to other cities.
        2. City set but unmapped ("Other") → Sales Manager.
        3. No city yet (a raw stage-1 enquiry) → phone country-code mapping,
           then team round-robin, then Sales Manager.
        """
        self.ensure_one()
        if self.mudon_branch_id:
            return (self._mudon_pick_by_branch()
                    or self._mudon_pick_sales_manager())
        if self.mudon_city_id:
            return self._mudon_pick_sales_manager()
        return (
            self._mudon_pick_by_country_code()
            or self._mudon_pick_round_robin()
            or self._mudon_pick_sales_manager()
        )

    def action_mudon_reassign_auto(self):
        """Re-run routing on demand (list/kanban action).

        Lets a manager fix the leads that were created before the routing
        bug was found, without re-keying each salesperson by hand.
        """
        for rec in self:
            if rec.mudon_pipeline_kind:
                rec._mudon_auto_assign_agent(force=True)
        return True

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
    def _mudon_send_whatsapp(self, phone, body, from_company=False,
                             template_key=None, template_params=None):
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
        # Name the line the message went out on. With a number per funnel
        # this is the only way to tell from the chatter whether a lead was
        # contacted on the Dubai or the Turkey line.
        sender = self.env["mudon.wa.sender"]._mudon_resolve(
            self.mudon_pipeline_kind, from_company=from_company)
        if sender:
            sender_label = "%s (%s)" % (
                sender.name, sender.display_number or sender.phone_number_id)
        elif from_company:
            sender_label = "COMPANY (%s)" % (
                company_no or "no-number-configured")
        else:
            sender_label = "AGENT"
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
                phone, body, from_company=from_company,
                template_key=template_key, template_params=template_params)
        _logger.warning(
            "mudon_crm: unknown WA provider '%s' — message NOT sent for "
            "lead %s.", provider, self.id,
        )
        return False

    # ─── Meta Cloud API send + delivery log + retry ─────────────────
    def _mudon_wa_log(self, to_number, body, status="sent", wamid="",
                      error="", from_company=False, direction="out"):
        """Append one row to the WhatsApp message log (audit + retry
        queue). Body is stored as plain text.

        Uses the same converter as the send path, so the log shows exactly
        what the recipient got — otherwise the log carries the footnote
        markers that were stripped before sending.
        """
        try:
            text = self._mudon_html_to_wa_text(body) if body else ""
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

    @staticmethod
    def _mudon_html_to_wa_text(body):
        """HTML to the plain text WhatsApp shows.

        `html2plaintext` turns links into academic-style footnotes — the
        agent alert went out reading "Link to WhatsApp Chat:
        https://wa.me/92... [1]" with a numbered list underneath. The URL
        is already visible inline, so the markers and the footnote block
        are noise. Strip both.
        """
        import re
        text = html2plaintext(body or "")
        # Trailing reference list: lines that are just "[1] http://..."
        text = re.sub(r"(?m)^\s*\[\d+\]\s+\S+\s*$", "", text)
        # Inline markers left behind next to the link itself.
        text = re.sub(r"\s*\[\d+\]", "", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _mudon_wa_meta_post(self, to_digits, text, from_company=False,
                            template=None, template_params=None):
        """Low-level POST of one message to the Meta Cloud API.

        Sends an approved template when one is given, otherwise plain
        text. Plain text only reaches people who messaged the business
        in the last 24 hours; outside that window Meta accepts it,
        returns a message id, and drops it. So a template is the only
        reliable way to start a conversation.

        Returns (ok, wamid, error, permanent). No DB writes — callers
        log the result."""
        ICP = self.env["ir.config_parameter"].sudo()
        token = ICP.get_param("mudon_crm.wa_access_token", "")
        api_version = ICP.get_param("mudon_crm.wa_api_version", "v21.0") or "v21.0"

        # One number per funnel, per the client's "1 for Dubai, 1 for
        # Turkey". A configured sender wins; with none configured this
        # falls through to the original single-number settings, so an
        # existing setup keeps working untouched.
        sender = self.env["mudon.wa.sender"]._mudon_resolve(
            self.mudon_pipeline_kind, from_company=from_company)
        if sender:
            phone_number_id = sender.phone_number_id
            # A per-number token only exists when that number sits under a
            # different business account; otherwise the WABA-wide token
            # in Settings covers it.
            token = sender.access_token or token
        elif from_company:
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
        if template:
            components = []
            if template_params:
                components.append({
                    "type": "body",
                    # Meta rejects a parameter containing a newline or a
                    # tab, and every one of ours is a single line, so
                    # flatten defensively rather than fail the send.
                    "parameters": [
                        {"type": "text",
                         "text": " ".join(str(p or "").split())}
                        for p in template_params
                    ],
                })
            payload = {
                "messaging_product": "whatsapp",
                "to": to_digits,
                "type": "template",
                "template": {
                    "name": template.template_name,
                    "language": {"code": template.lang_code or "en"},
                },
            }
            if components:
                payload["template"]["components"] = components
        else:
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

    def _mudon_send_whatsapp_meta(self, phone, body, from_company=False,
                                  template_key=None, template_params=None):
        """Send via Meta Cloud API + log the result. Keeps a chatter copy
        on success so the CRM card still shows what went out.

        `body` stays the source of truth for the chatter and the log even
        when a template carries the actual send — the two say the same
        thing, and the reader wants the readable version.
        """
        self.ensure_one()
        to_digits = (self._mudon_phone_normalize(phone) or "").lstrip("+")
        try:
            text = self._mudon_html_to_wa_text(body) if body else ""
        except Exception:
            text = str(body or "")
        template = self.env["mudon.wa.template"]._mudon_for(template_key)
        ok, wamid, error, permanent = self._mudon_wa_meta_post(
            to_digits, text, from_company=from_company,
            template=template, template_params=template_params)
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
        # A brand-new lead has never messaged Mudon, so there is no
        # 24-hour window and only a template will reach them.
        self._mudon_send_whatsapp(
            self.phone, body, template_key="new_lead_greeting")

    # Escalation climbs the Employees app hierarchy, at the client's
    # request: the org chart people already maintain in Employees decides
    # who hears that a lead has gone cold, rather than a second structure
    # kept only inside the CRM.
    MUDON_ESCALATION_LEVELS = 3

    def _mudon_escalation_managers(self):
        """Managers to alert, nearest first.

        Walks Employees > Manager upward from the assigned agent, so a
        lead nobody touches surfaces past the first manager rather than
        stopping with them. Anyone without a phone is skipped but still
        walked through, since their own manager should still hear.

        Falls back to the pipeline's Sales Team Leader when the agent has
        no employee record or no manager above them — which is also what
        the spec means by "assign to Sales Manager".
        """
        self.ensure_one()
        seen, out = set(), self.env["res.users"]
        employee = self.user_id.employee_id if self.user_id else False
        depth = 0
        while employee and employee.parent_id and depth < self.MUDON_ESCALATION_LEVELS:
            employee = employee.parent_id
            depth += 1
            user = employee.user_id
            if not user or user.id in seen:
                continue
            seen.add(user.id)
            if user.phone:
                out |= user
        if not out:
            leader = self.team_id.user_id
            if leader and leader.phone:
                out = leader
        return out

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
        # One template serves every agent/manager alert: they share this
        # body and differ only by the headline, which is parameter 1.
        tparams = [
            headline,
            self.contact_name or self.name or "(no name)",
            wa_link,
            card_link,
        ]
        if to_agent and self.user_id and self.user_id.phone:
            self._mudon_send_whatsapp(
                self.user_id.phone, body,
                template_key="agent_alert", template_params=tparams)
        if to_manager:
            for mgr in self._mudon_escalation_managers():
                self._mudon_send_whatsapp(
                    mgr.phone, body,
                    template_key="agent_alert", template_params=tparams)
        if to_marketing:
            # Marketing role = team manager fallback; client to configure
            # a dedicated marketing user via ir.config_parameter later.
            mkt_uid = int(self.env["ir.config_parameter"].sudo().get_param(
                "mudon_crm.marketing_user_id", "0",
            ) or 0)
            mkt = self.env["res.users"].browse(mkt_uid) if mkt_uid else False
            if mkt and mkt.phone:
                self._mudon_send_whatsapp(
                    mkt.phone, body,
                    template_key="agent_alert", template_params=tparams)
            elif self.team_id.user_id and self.team_id.user_id.phone:
                self._mudon_send_whatsapp(
                    self.team_id.user_id.phone, body,
                    template_key="agent_alert", template_params=tparams)

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
            # Reuses the agent template — this also goes to a staff member
            # and needs a window they will not have. The reason travels in
            # the headline so nothing is lost against the plain-text body.
            base_url = self.env["ir.config_parameter"].sudo().get_param(
                "web.base.url", "")
            self._mudon_send_whatsapp(
                target.phone, body,
                template_key="agent_alert",
                template_params=[
                    "%s — %s" % (_("Client Lost"), reason_label),
                    self.contact_name or self.name or "(no name)",
                    "https://wa.me/%s" % (
                        self._mudon_phone_normalize(self.phone).lstrip("+")
                        or ""),
                    "%s/odoo/action-crm.crm_lead_action_pipeline/%s" % (
                        base_url, self.id),
                ])

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
        self._mudon_send_whatsapp(
            self.phone, body, from_company=True,
            template_key="client_survey",
            template_params=[self.contact_name or self.name or "(no name)"])

    # ─── SLA cron handlers ──────────────────────────────────────────
    # Every delay below is read from Settings at run time (client comment
    # 6) rather than hard-coded, so the client can retune the automation
    # without a code deploy. The defaults match the original spec exactly,
    # so behaviour is unchanged until someone edits a value.
    @api.model
    def _mudon_timer(self, key, default):
        """One notification timer from Settings, guarded.

        A blank or non-numeric value falls back to the spec default rather
        than disabling the reminder — a mistyped setting must never
        silently switch off a client-facing SLA. A value of 0 IS honoured
        and means "fire on the next sweep".

        NOTE: ``get_param`` returns the boolean ``False`` for a key that has
        never been written, and ``int(False)`` is 0 rather than an error.
        Without the emptiness check below, every timer would read as 0 until
        somebody opened Settings and pressed Save — so all reminders would
        fire on the first sweep after a lead was created.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "mudon_crm.%s" % key)
        if raw is False or raw is None or str(raw).strip() == "":
            return default
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return default
        return value if value >= 0 else default

    @api.model
    def _mudon_cron_sla_30min(self):
        """Stage 1 — first reminder if no agent contact (default 30 min)."""
        threshold = fields.Datetime.now() - timedelta(
            minutes=self._mudon_timer("sla_new_lead_first", 30))
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
        """Stage 1 — escalation to agent + manager (default 60 min)."""
        threshold = fields.Datetime.now() - timedelta(
            minutes=self._mudon_timer("sla_new_lead_escalate", 60))
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
        """Stage 2 — first reminder if no contact (default 30 min)."""
        threshold = fields.Datetime.now() - timedelta(
            minutes=self._mudon_timer("sla_qualified_first", 30))
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
        """Stage 2 — escalation to the manager (default 120 min)."""
        threshold = fields.Datetime.now() - timedelta(
            minutes=self._mudon_timer("sla_qualified_escalate", 120))
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
        threshold = fields.Datetime.now() - timedelta(
            hours=self._mudon_timer("sla_offer_followup", 3))
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
        target = fields.Date.today() + timedelta(
            days=self._mudon_timer("sla_visit_notice", 15))
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
        cadence = self._mudon_timer("sla_stay_in_touch", 15)
        leads = self.search([
            ("mudon_visit_date", ">", today + timedelta(days=cadence)),
            ("stage_id.id", "in", self._mudon_stage_ids("offer_sent")),
        ])
        fired = 0
        for lead in leads:
            last = lead.mudon_last_15day_reminder_date
            if last and (today - last).days < cadence:
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
            ("mudon_offer_counter", ">=",
             self._mudon_timer("survey_after_offer", 3)),
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
        # Which days-before to remind on is configurable (comment 6); the
        # three persisted flags cap it at three distinct reminders.
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "mudon_crm.meeting_reminder_days", "3,2,1") or "3,2,1"
        try:
            wanted = [int(x) for x in raw.split(",") if x.strip()][:3]
        except ValueError:
            wanted = [3, 2, 1]
        flags = ("mudon_meeting_reminder_3day_sent",
                 "mudon_meeting_reminder_2day_sent",
                 "mudon_meeting_reminder_1day_sent")
        for days, flag in zip(wanted or [3, 2, 1], flags):
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
        team_id = self.env.context.get("default_team_id")
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
        # Only a deliberate message from the agent counts as contact.
        #
        # Odoo posts its own "Opportunity Created" and "you have been
        # assigned" entries DURING create, while user_id still points at
        # whoever keyed the lead in — so the author test below matched and
        # every new lead was marked contacted before the agent had even
        # seen it. Routing then moved the lead to the real agent, hiding
        # the cause. The effect was that no SLA chase and no manager
        # escalation ever fired: 31 of 59 existing leads are stuck that
        # way. Those automatic entries are notes and system notifications;
        # a real agent message is a comment.
        if kwargs.get("subtype_xmlid") != "mail.mt_comment":
            return res
        if self.mudon_pipeline_kind and not self.mudon_first_contact_logged:
            author = kwargs.get("author_id") or self.env.user.partner_id.id
            if self.user_id and self.user_id.partner_id.id == author:
                self.sudo().with_context(
                    mudon_in_write=True,
                ).write({"mudon_first_contact_logged": True})
        return res
