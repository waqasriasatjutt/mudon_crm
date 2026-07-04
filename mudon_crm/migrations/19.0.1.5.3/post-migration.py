"""Post-migration for mudon_crm 19.0.1.5.3.

Delete Odoo's stock global CRM stages (New / Qualified / Proposition /
Won) so the pipeline stops mixing them with the per-team Mudon stages,
and new leads start on 'New Lead'. Reuses the module's cleanup helper so
fresh installs (post_init_hook) and upgrades stay in sync.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.mudon_crm import _mudon_cleanup_default_stages


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _mudon_cleanup_default_stages(env)
