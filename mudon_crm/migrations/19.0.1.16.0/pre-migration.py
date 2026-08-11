"""Field-change round of 2026-08-10 — the two type changes.

Both of these swap a column's type under existing data, so the old column
is parked here and read back in post-migration. Letting Odoo alter the
type in place would either fail outright or quietly blank the values.

  * `mudon_beds` Integer -> Selection. "Studio" is not a number, so the
    button row the client asked for cannot be an Integer.
  * `mudon_commission` Monetary -> stored compute off a new percentage.
    The already-entered amounts are what the percentages get derived from.
"""


def migrate(cr, version):
    if not version:
        return

    for old, new in (
        ("mudon_beds", "mudon_beds_old_int"),
        ("mudon_commission", "mudon_commission_old_amount"),
    ):
        cr.execute("""
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'crm_lead' AND column_name = %s
        """, (old,))
        if not cr.fetchone():
            continue
        # A re-run of the same migration must not clobber the parked copy.
        cr.execute("""
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'crm_lead' AND column_name = %s
        """, (new,))
        if cr.fetchone():
            cr.execute('ALTER TABLE crm_lead DROP COLUMN "%s"' % old)
        else:
            cr.execute(
                'ALTER TABLE crm_lead RENAME COLUMN "%s" TO "%s"' % (old, new))
