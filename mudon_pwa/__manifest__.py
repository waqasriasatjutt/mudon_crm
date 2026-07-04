{
    "name": "Mudon CRM — Mobile App (PWA)",
    "version": "19.0.1.0.0",
    "summary": "Installable, branded Mudon CRM progressive web app + a light "
               "mobile polish layer for the backend.",
    "description": "Rebrands the Odoo 19 PWA as the Mudon CRM app — custom "
                   "name, icons, theme colour and a start page that lands on "
                   "the CRM pipeline — so staff can install it to a phone / "
                   "tablet home screen and run it full-screen like a native "
                   "app (no app store, no separate build). Adds a small, "
                   "width-gated mobile polish layer (tap targets, scrollable "
                   "stage bar).",
    "category": "Sales/CRM",
    "author": "Waqas Riasat",
    "website": "https://way4tech.com",
    "license": "OPL-1",
    "depends": ["web", "mudon_crm"],
    "data": [
        "data/pwa_config.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mudon_pwa/static/src/scss/mudon_mobile.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
