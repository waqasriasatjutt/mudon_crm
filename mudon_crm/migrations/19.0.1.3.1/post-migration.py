"""Post-migration for mudon_crm 19.0.1.3.1.

Activate AED on upgrade. The UAE Dubai pipeline bills in AED, but a base
currency's xmlid is noupdate, so a data-file `<record id="base.AED">`
cannot flip `active`. Fresh installs handle this in post_init_hook; this
covers existing tenants upgrading in place.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    aed = env.ref("base.AED", raise_if_not_found=False)
    if aed and not aed.active:
        aed.active = True
        _logger.info("mudon_crm 1.3.1: activated AED currency")
