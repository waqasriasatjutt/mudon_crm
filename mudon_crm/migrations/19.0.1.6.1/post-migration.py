"""Post-migration for mudon_crm 19.0.1.6.1.

The custom "Budget" field is retired in favour of Odoo's native
Expected Revenue (used across Odoo reporting). Carry any value that
was captured in the old Budget over to Expected Revenue so existing
leads don't lose their figure.
"""


def migrate(cr, version):
    cr.execute(
        """
        UPDATE crm_lead
           SET expected_revenue = mudon_budget
         WHERE (expected_revenue IS NULL OR expected_revenue = 0)
           AND mudon_budget IS NOT NULL
           AND mudon_budget > 0
        """
    )
