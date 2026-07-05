{
    "name": "Mudon CRM — Dashboards",
    "version": "19.0.1.3.0",
    "summary": "Management & Financial reporting dashboards for Mudon CRM "
               "(OWL + Chart.js) — funnel, agent performance, revenue & "
               "commission, per client spec.",
    "description": "Separate reporting layer on top of mudon_crm. Adds an "
                   "interactive Management dashboard (assigned→won funnel, "
                   "agent performance, leads by country/source, monthly "
                   "commission trend) with pipeline / period / basis filters.",
    "category": "Sales/CRM",
    "author": "Waqas Riasat",
    "website": "https://way4tech.com",
    "license": "OPL-1",
    "depends": ["mudon_crm", "web"],
    "data": [
        "security/ir.model.access.csv",
        "views/mudon_dashboard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mudon_dashboard/static/src/dashboard/mudon_dashboard.scss",
            "mudon_dashboard/static/src/dashboard/mudon_dashboard.js",
            "mudon_dashboard/static/src/dashboard/mudon_dashboard.xml",
            "mudon_dashboard/static/src/dashboard/financial_dashboard.js",
            "mudon_dashboard/static/src/dashboard/financial_dashboard.xml",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
