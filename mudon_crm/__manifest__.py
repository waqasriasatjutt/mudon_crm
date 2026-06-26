{
    "name": "Mudon Property — CRM",
    "version": "19.0.1.1.0",
    "summary": "Mudon Property real-estate CRM — Turkey CBI + UAE Dubai "
               "Golden-Visa pipelines, 7-stage flow, country-code & "
               "branch routing, WhatsApp greetings + agent + company "
               "sends, multi-stage SLA escalations, Admin + After-Sales "
               "funnels, META Cloud inbound webhook.",
    "description": "M1-M7 full build per proposal WR-2026-MUDON-CRM.",
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
        "data/branch_data.xml",
        "data/mail_template_data.xml",
        "data/ir_cron_data.xml",
        "views/country_agent_mapping_views.xml",
        "views/branch_views.xml",
        "views/developer_views.xml",
        "views/admin_task_views.xml",
        "views/after_sales_task_views.xml",
        "views/crm_lead_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
