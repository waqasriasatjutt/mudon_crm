"""Pre-migration for mudon_crm 19.0.1.4.0.

Two field-type changes on `crm.lead` — the ORM can't convert either in
place, so we do it in SQL before the upgrade re-creates the columns:

  1. mudon_visit_confirmed: Selection (varchar) → Boolean
     - Old values: 'yes' / 'no' / NULL
     - We stash the old varchar as `_mudon_visit_confirmed_bak` so the
       post-migration can set the new bool column from it. The old
       column is dropped when the ORM notices the field-type change.

  2. mudon_city_ids (Many2many) → mudon_city_id (Many2one)
     - The Many2many stored data in `crm_lead_mudon_city_rel`.
     - We DO NOT add the new column here (ORM does that at load). Post-
       migration picks the first city from the rel table per lead and
       writes it to the new column.

Guarded + idempotent — a repeat upgrade is a no-op.
"""
import logging

_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    # Stash the old Selection column so post-migration can read it after
    # the ORM has replaced mudon_visit_confirmed with a boolean column.
    if _column_exists(cr, "crm_lead", "mudon_visit_confirmed") \
            and not _column_exists(cr, "crm_lead", "_mudon_visit_confirmed_bak"):
        cr.execute("""
            ALTER TABLE crm_lead
             ADD COLUMN _mudon_visit_confirmed_bak varchar
        """)
        cr.execute("""
            UPDATE crm_lead
               SET _mudon_visit_confirmed_bak = mudon_visit_confirmed
             WHERE mudon_visit_confirmed IS NOT NULL
        """)
        _logger.info(
            "mudon_crm 1.4.0 pre: stashed %s mudon_visit_confirmed value(s) "
            "before Selection→Boolean conversion",
            cr.rowcount,
        )
