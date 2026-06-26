{
    "name": "Mudon Property — CRM",
    "version": "19.0.1.0.0",
    "summary": "Mudon Property real-estate CRM — Turkey CBI + UAE Dubai "
               "Golden-Visa pipelines, country-code lead routing, "
               "WhatsApp greetings, SLA escalations.",
    "description": "M1 (New Lead stage) — proposal WR-2026-MUDON-CRM. "
                   "Adds two CRM pipelines (Mudon Turkey, Mudon UAE Dubai) "
                   "with stage-1 fields, auto-assignment by country code, "
                   "client + agent WhatsApp messaging, and 30-min / 1-hour "
                   "SLA reminders.",
    "category": "Sales/CRM",
    "author": "Waqas Riasat",
    "website": "https://way4tech.com",
    "license": "OPL-1",
    "depends": [
        "crm",
        "mail",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/res_users_data.xml",
        "data/crm_team_data.xml",
        "data/crm_stage_data.xml",
        "data/country_agent_mapping_data.xml",
        "data/mail_template_data.xml",
        "data/ir_cron_data.xml",
        "views/country_agent_mapping_views.xml",
        "views/crm_lead_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
