"""Carry the parked values into the new columns, then drop the copies.

Runs after Odoo has created `mudon_beds` as a varchar and
`mudon_commission` as the stored compute.
"""
import logging

_logger = logging.getLogger(__name__)


def _has_column(cr, name):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'crm_lead' AND column_name = %s
    """, (name,))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return

    # ── No. of Beds: 0 reads as Studio, 1..6 map across, anything above
    #    6 lands on 6 because the client's button row stops there.
    if _has_column(cr, "mudon_beds_old_int") and _has_column(cr, "mudon_beds"):
        cr.execute("""
            UPDATE crm_lead
               SET mudon_beds = CASE
                     WHEN mudon_beds_old_int = 0 THEN 'studio'
                     WHEN mudon_beds_old_int BETWEEN 1 AND 6
                          THEN mudon_beds_old_int::text
                     WHEN mudon_beds_old_int > 6 THEN '6'
                   END
             WHERE mudon_beds_old_int IS NOT NULL
        """)
        _logger.info("mudon_crm: carried %s bed counts over", cr.rowcount)
        cr.execute("ALTER TABLE crm_lead DROP COLUMN mudon_beds_old_int")

    # ── Commission: derive the percentage from what was already entered,
    #    so a won deal keeps the same figure once the compute reruns.
    #    Deals with no closing amount have nothing to derive from; their
    #    old amount is reported rather than silently dropped.
    if _has_column(cr, "mudon_commission_old_amount"):
        cr.execute("""
            UPDATE crm_lead
               SET mudon_commission_pct =
                     ROUND((mudon_commission_old_amount
                            / mudon_closing_amount * 100)::numeric, 2)
             WHERE mudon_commission_old_amount IS NOT NULL
               AND mudon_commission_old_amount != 0
               AND COALESCE(mudon_closing_amount, 0) != 0
        """)
        derived = cr.rowcount
        cr.execute("""
            SELECT count(*) FROM crm_lead
             WHERE COALESCE(mudon_commission_old_amount, 0) != 0
               AND COALESCE(mudon_closing_amount, 0) = 0
        """)
        orphaned = cr.fetchone()[0]
        if orphaned:
            _logger.warning(
                "mudon_crm: %s lead(s) had a commission amount but no closing "
                "amount, so no percentage could be derived. Re-enter the "
                "percentage on those deals.", orphaned)
        _logger.info("mudon_crm: derived %s commission percentages", derived)
        cr.execute(
            "ALTER TABLE crm_lead DROP COLUMN mudon_commission_old_amount")

    # The compute is stored, so existing rows need it run once.
    cr.execute("""
        UPDATE crm_lead
           SET mudon_commission = ROUND(
                 (COALESCE(mudon_closing_amount, 0)
                  * COALESCE(mudon_commission_pct, 0) / 100)::numeric, 2)
    """)
