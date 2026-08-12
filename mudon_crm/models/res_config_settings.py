from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Settings panel so the client can enter / rotate the Meta WhatsApp
    credentials and switch the CRM between stub (log-only) and live
    sending — no code deploy needed. Backed by ir.config_parameter, so
    the token is stored on the live system only, never in the repo."""

    _inherit = "res.config.settings"

    mudon_wa_provider = fields.Selection(
        [("stub", "Stub — log messages only (safe)"),
         ("meta", "Meta Cloud API — send for real")],
        string="WhatsApp mode",
        config_parameter="mudon_crm.wa_provider",
        default="stub",
        help="Keep on Stub until the Meta credentials below are set and "
             "tested. Switch to Meta Cloud API to start sending.",
    )
    mudon_wa_access_token = fields.Char(
        string="Meta access token",
        config_parameter="mudon_crm.wa_access_token",
        help="Permanent System-User token from Meta Business "
             "(whatsapp_business_messaging scope). Live-only — never "
             "committed to code.",
    )
    mudon_wa_waba_id = fields.Char(
        string="WhatsApp Business Account ID",
        config_parameter="mudon_crm.wa_waba_id",
        help="From Meta > WhatsApp > API Setup. The account both numbers "
             "sit under. Recorded for reference; sending does not need it.",
    )
    mudon_wa_phone_number_id = fields.Char(
        string="Default phone-number ID",
        config_parameter="mudon_crm.wa_phone_number_id",
        help="Used only when no number is configured for the lead's "
             "pipeline. For one number per funnel, use Configuration > "
             "WhatsApp Numbers instead.",
    )
    mudon_wa_company_phone_number_id = fields.Char(
        string="Company phone-number ID",
        config_parameter="mudon_crm.wa_company_phone_number_id",
        help="Phone-number id for the separate company number used by the "
             "3rd-offer client survey. Falls back to the sending number "
             "if blank.",
    )
    mudon_wa_company_number = fields.Char(
        string="Company number (display)",
        config_parameter="mudon_crm.wa_company_number",
    )
    mudon_wa_verify_token = fields.Char(
        string="Webhook verify token",
        config_parameter="mudon_crm.wa_verify_token",
        help="Any secret string; enter the same value in the Meta App "
             "webhook configuration. Callback URL is "
             "<your-domain>/mudon/wa/webhook.",
    )
    mudon_wa_api_version = fields.Char(
        string="Graph API version",
        config_parameter="mudon_crm.wa_api_version",
        default="v21.0",
    )
    # Comment 16 — "For lost card, what happens to them when they
    # accumulate? Can I archive?" They can, by hand, at any time. This
    # makes it automatic so the Lost column does not grow forever.
    mudon_lost_archive_days = fields.Integer(
        string="Auto-archive lost leads after (days)",
        config_parameter="mudon_crm.lost_archive_days",
        help="Lost leads older than this are archived automatically each "
             "night. Archived means hidden from the pipeline, not deleted "
             "— they stay searchable under the Archived filter and in "
             "reporting. Set 0 to switch it off.",
    )
    mudon_marketing_user_id = fields.Many2one(
        "res.users",
        string="Marketing user (Lost alerts)",
        config_parameter="mudon_crm.marketing_user_id",
        help="Receives the WhatsApp alert when a lead is marked Lost. "
             "Falls back to the sales-team manager if unset.",
    )

    # ─── Meta Lead Ads ──────────────────────────────────────────────────
    mudon_meta_page_token = fields.Char(
        string="Meta page access token",
        config_parameter="mudon_crm.meta_page_token",
        help="A PAGE token, not the WhatsApp one. It needs the "
             "leads_retrieval permission and access to the page the ads run "
             "from. Generate it against the System User that owns the page.",
    )
    mudon_meta_app_secret = fields.Char(
        string="Meta app secret",
        config_parameter="mudon_crm.meta_app_secret",
        help="From the App dashboard, Settings > Basic. Used to verify that "
             "an incoming lead really came from Meta. Leave blank only "
             "while testing.",
    )
    mudon_meta_verify_token = fields.Char(
        string="Lead webhook verify token",
        config_parameter="mudon_crm.meta_verify_token",
        help="Any secret word. Enter the same value on the Meta webhook "
             "page. Callback URL is <your-domain>/mudon/meta/leadgen.",
    )

    # ─── Notification timers (client comment 6) ─────────────────────────
    # "WhatsApp Automation Notification messages timers to be added to
    # configuration". Every delay the SLA engine used to hard-code is now
    # editable here; the crons read these at run time, so a change takes
    # effect on the next sweep with no upgrade and no downtime.
    mudon_sla_new_lead_first = fields.Integer(
        string="New Lead — first reminder (minutes)",
        config_parameter="mudon_crm.sla_new_lead_first",
        default=30,
        help="Minutes after a new lead arrives before the assigned agent is "
             "reminded that the client has not been contacted. Spec: 30.",
    )
    mudon_sla_new_lead_escalate = fields.Integer(
        string="New Lead — escalation to manager (minutes)",
        config_parameter="mudon_crm.sla_new_lead_escalate",
        default=60,
        help="Minutes before the New Lead reminder escalates to the agent "
             "AND the manager. Spec: 60.",
    )
    mudon_sla_qualified_first = fields.Integer(
        string="Qualified — first reminder (minutes)",
        config_parameter="mudon_crm.sla_qualified_first",
        default=30,
        help="Minutes on the Qualified stage without contact before the "
             "agent is reminded. Spec: 30.",
    )
    mudon_sla_qualified_escalate = fields.Integer(
        string="Qualified — escalation to manager (minutes)",
        config_parameter="mudon_crm.sla_qualified_escalate",
        default=120,
        help="Minutes on the Qualified stage without contact before the "
             "manager is copied in. Spec: 2 hours = 120.",
    )
    mudon_sla_offer_followup = fields.Integer(
        string="Offer Sent — follow-up reminder (hours)",
        config_parameter="mudon_crm.sla_offer_followup",
        default=3,
        help="Hours after each offer is sent before the agent is reminded "
             "to chase feedback. Spec: 3.",
    )
    mudon_sla_visit_notice = fields.Integer(
        string="Offer Sent — days before visit reminder (days)",
        config_parameter="mudon_crm.sla_visit_notice",
        default=15,
        help="Days before the Expected Visit Date that the agent is asked "
             "to confirm the visit. Spec: 15.",
    )
    mudon_sla_stay_in_touch = fields.Integer(
        string="Offer Sent — stay-in-touch cadence (days)",
        config_parameter="mudon_crm.sla_stay_in_touch",
        default=15,
        help="How often to nudge the agent while the visit is still far "
             "away, so leads do not go cold. Spec: every 15 days.",
    )
    mudon_survey_after_offer = fields.Integer(
        string="Client survey after N offers",
        config_parameter="mudon_crm.survey_after_offer",
        default=3,
        help="Number of offers after which the company number sends the "
             "client the structured survey. Spec: 3.",
    )
    mudon_meeting_reminder_days = fields.Char(
        string="Meeting reminders — days before",
        config_parameter="mudon_crm.meeting_reminder_days",
        default="3,2,1",
        help="Comma-separated list of days-before-meeting to remind the "
             "agent. Spec: 3,2,1.",
    )
