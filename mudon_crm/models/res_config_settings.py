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
    mudon_wa_phone_number_id = fields.Char(
        string="Sending phone-number ID",
        config_parameter="mudon_crm.wa_phone_number_id",
        help="The WhatsApp phone-number id agents / business flows send "
             "from (from Meta > WhatsApp > API Setup).",
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
    mudon_marketing_user_id = fields.Many2one(
        "res.users",
        string="Marketing user (Lost alerts)",
        config_parameter="mudon_crm.marketing_user_id",
        help="Receives the WhatsApp alert when a lead is marked Lost. "
             "Falls back to the sales-team manager if unset.",
    )
