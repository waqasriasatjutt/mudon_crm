def migrate(cr, version):
    """Carry existing template wording into the plain-language field.

    Templates used to be written with Meta's own {{1}}, {{2}} markers.
    They are now written in words, so what is already on file is
    translated back: {{2}} becomes {client name} and so on.

    This runs PRE-migration deliberately. `body_text` has become a stored
    computed field derived FROM the new wording field, so once the module
    loads Odoo recomputes it, and against an empty wording it would
    recompute to nothing. Reading the old value has to happen before that,
    which means creating the new column here by hand.
    """
    # A database old enough to predate the template model has no table to
    # alter, and one at exactly 19.0.1.19.0 has the table but neither
    # body_text nor meta_body. Either way this must not abort the upgrade.
    cr.execute("SELECT to_regclass('mudon_wa_template')")
    if not cr.fetchone()[0]:
        return
    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'mudon_wa_template'
           AND column_name IN ('body_text', 'meta_body')
    """)
    if not cr.fetchall():
        return
    cr.execute("""
        ALTER TABLE mudon_wa_template
          ADD COLUMN IF NOT EXISTS body_wording text
    """)
    tokens = {
        "new_lead_greeting": [],
        "agent_alert": ["what happened", "client name",
                        "whatsapp link", "card link"],
        "client_survey": ["client name"],
    }
    # Fall back to the wording Meta has on file. A template approved
    # before this screen could author it has an empty local body and the
    # real wording only in `meta_body`, so copying `body_text` alone left
    # the box blank on exactly the templates that are live.
    cr.execute("""
        SELECT id, message_key,
               COALESCE(NULLIF(body_text, ''), meta_body)
          FROM mudon_wa_template
         WHERE COALESCE(NULLIF(body_text, ''), meta_body) IS NOT NULL
    """)
    for rec_id, key, body in cr.fetchall():
        wording = body
        for i, tok in enumerate(tokens.get(key) or [], 1):
            wording = wording.replace("{{%d}}" % i, "{%s}" % tok)
        cr.execute(
            "UPDATE mudon_wa_template SET body_wording = %s WHERE id = %s",
            (wording, rec_id))
