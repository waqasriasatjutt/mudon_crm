"""Post-migration for mudon_crm 19.0.1.4.0.

Two data moves that follow the pre-migration schema shuffle:

  1. mudon_visit_confirmed Selection ('yes'/'no'/NULL) → Boolean
     Read from the _mudon_visit_confirmed_bak column we stashed in the
     pre-migration and set the new boolean.

  2. mudon_city_ids (Many2many) → mudon_city_id (Many2one)
     Take the FIRST city from crm_lead_mudon_city_rel per lead and
     write it to the new mudon_city_id column. "First" = lowest
     rel-row id, which matches insertion order for our tags widget.
     The old rel table lingers until the module install cleans it up,
     which is fine — it's harmless and idempotent to re-run.

Both steps are guarded — a repeat upgrade is a no-op.
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


def _table_exists(cr, table):
    cr.execute(
        """
        SELECT 1 FROM information_schema.tables
         WHERE table_name = %s
        """,
        (table,),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    # -- 1. Convert mudon_visit_confirmed Selection → Boolean ---------
    if _column_exists(cr, "crm_lead", "_mudon_visit_confirmed_bak") \
            and _column_exists(cr, "crm_lead", "mudon_visit_confirmed"):
        cr.execute("""
            UPDATE crm_lead
               SET mudon_visit_confirmed = TRUE
             WHERE _mudon_visit_confirmed_bak = 'yes'
        """)
        yes_rows = cr.rowcount
        cr.execute("""
            UPDATE crm_lead
               SET mudon_visit_confirmed = FALSE
             WHERE mudon_visit_confirmed IS NULL
        """)
        cr.execute("ALTER TABLE crm_lead DROP COLUMN _mudon_visit_confirmed_bak")
        _logger.info(
            "mudon_crm 1.4.0 post: converted %s 'yes' value(s) to True; "
            "NULLs coerced to False; dropped stash column",
            yes_rows,
        )

    # -- 2. Take FIRST city from rel table → mudon_city_id ------------
    if _column_exists(cr, "crm_lead", "mudon_city_id") \
            and _table_exists(cr, "crm_lead_mudon_city_rel"):
        cr.execute("""
            UPDATE crm_lead l
               SET mudon_city_id = sub.city_id
              FROM (
                    SELECT DISTINCT ON (lead_id) lead_id, city_id
                      FROM crm_lead_mudon_city_rel
                     ORDER BY lead_id, id
                   ) sub
             WHERE sub.lead_id = l.id
               AND l.mudon_city_id IS NULL
        """)
        _logger.info(
            "mudon_crm 1.4.0 post: set mudon_city_id on %s lead(s) from the "
            "old Many2many relation",
            cr.rowcount,
        )
