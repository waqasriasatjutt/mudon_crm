from . import models
from . import controllers
from . import wizards


def post_init_hook(env):
    """Activate AED on fresh install — the UAE Dubai pipeline bills in AED.
    A base currency's xmlid is noupdate, so it can't be flipped from a data
    file; do it in code instead. (Upgrades are handled by the matching
    migration script.)"""
    aed = env.ref("base.AED", raise_if_not_found=False)
    if aed and not aed.active:
        aed.active = True
