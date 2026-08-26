def migrate(cr, version):
    """Strip '+', spaces and leading zeros from saved dialling codes.

    Routing matches these against the digits of the lead's phone, so a
    code entered as "+966" never matched and the rule quietly did
    nothing. The field now cleans itself on save; this repairs the rows
    written before that.
    """
    cr.execute("""
        SELECT id, country_code
          FROM mudon_country_agent_mapping
         WHERE country_code IS NOT NULL
    """)
    for rec_id, code in cr.fetchall():
        digits = "".join(ch for ch in (code or "") if ch.isdigit())
        digits = digits.lstrip("0") or digits
        if digits and digits != code:
            # A clean row may already own this code+team; keep that one
            # and drop the malformed duplicate rather than break the
            # unique constraint.
            cr.execute("""
                SELECT id FROM mudon_country_agent_mapping
                 WHERE country_code = %s
                   AND team_id IS NOT DISTINCT FROM (
                        SELECT team_id FROM mudon_country_agent_mapping
                         WHERE id = %s)
                   AND id != %s
            """, (digits, rec_id, rec_id))
            clash = cr.fetchone()
            if clash:
                # Carry the malformed row's agent onto the surviving row
                # and re-activate it, so the admin's intent is kept.
                cr.execute("""
                    UPDATE mudon_country_agent_mapping AS keep
                       SET agent_user_id = bad.agent_user_id,
                           active = TRUE
                      FROM mudon_country_agent_mapping AS bad
                     WHERE keep.id = %s AND bad.id = %s
                """, (clash[0], rec_id))
                cr.execute(
                    "DELETE FROM mudon_country_agent_mapping WHERE id = %s",
                    (rec_id,))
            else:
                cr.execute("""
                    UPDATE mudon_country_agent_mapping
                       SET country_code = %s
                     WHERE id = %s
                """, (digits, rec_id))
