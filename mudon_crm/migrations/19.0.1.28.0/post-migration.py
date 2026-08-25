"""Carry the single bed choice onto the new multi-choice field.

The client asked for No. of Beds to accept several values, the way
Property Type does, so the single Selection becomes a Many2many. Anything
already recorded is matched to the new list by its code.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'crm_lead' AND column_name = 'mudon_beds'
    """)
    if not cr.fetchone():
        return
    cr.execute("""
        INSERT INTO crm_lead_mudon_bed_count_rel (lead_id, bed_id)
        SELECT l.id, b.id
          FROM crm_lead l
          JOIN mudon_bed_count b ON b.code = l.mudon_beds
         WHERE l.mudon_beds IS NOT NULL
        ON CONFLICT DO NOTHING
    """)
    _logger.info("mudon_crm: carried %s bed selections onto the new list",
                 cr.rowcount)
