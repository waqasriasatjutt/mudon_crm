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
