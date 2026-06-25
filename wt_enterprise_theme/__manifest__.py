{
    'name': 'WT Odoo Enterprise Theme',
    'version': '19.0.1.0.0',
    'summary': 'Enterprise look & feel for Odoo 19 Community — white navbar, full-page home menu, purple brand. No subscription required.',
    'description': 'Way4Tech WT Odoo Enterprise Theme for Odoo 19 Community Edition.',
    'category': 'Themes/Backend',
    'author': 'Waqas Riasat',
    'maintainer': 'Waqas Riasat',
    'company': 'Waqas Riasat',
    'website': 'https://way4tech.com',
    'support': 'waqasriasatjutt@gmail.com',
    'license': 'OPL-1',
    'price': 10.0,
    'currency': 'USD',
    'images': [
        'static/description/banner.svg',
        'static/description/Screenshot_1.png',
        'static/description/Screenshot_2.png',
        'static/description/Screenshot_3.png',
    ],
    'depends': ['web'],
    'excludes': ['web_enterprise'],
    'application': False,
    'installable': True,
    'auto_install': False,
    'sequence': 10,
    'assets': {
        'web._assets_backend_helpers': [
            (
                'before',
                'web/static/src/scss/bootstrap_overridden.scss',
                'wt_enterprise_theme/static/src/scss/bootstrap_overridden.scss',
            ),
        ],
        'web._assets_primary_variables': [
            (
                'before',
                'web/static/src/scss/primary_variables.scss',
                'wt_enterprise_theme/static/src/scss/primary_variables.scss',
            ),
            'wt_enterprise_theme/static/src/scss/home_menu.variables.scss',
        ],
        'web.assets_frontend': [
            # Login page gets the same gradient background
            'wt_enterprise_theme/static/src/scss/home_menu_background.scss',
        ],
        'web.assets_backend': [
            'wt_enterprise_theme/static/src/scss/home_menu_background.scss',
            'wt_enterprise_theme/static/src/scss/home_menu.scss',
            'wt_enterprise_theme/static/src/scss/navbar.scss',
            'wt_enterprise_theme/static/src/js/home_menu.js',
            'wt_enterprise_theme/static/src/js/home_menu_service.js',
            'wt_enterprise_theme/static/src/js/enterprise_patch.js',
            'wt_enterprise_theme/static/src/xml/home_menu.xml',
        ],
    },
}
