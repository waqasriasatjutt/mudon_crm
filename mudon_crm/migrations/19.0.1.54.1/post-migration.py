# -*- coding: utf-8 -*-
def migrate(cr, version):
    """Deactivate Odoo's stock "Personal Leads" record rule.

    It grants a salesman `user_id = self OR user_id = False`; the False half
    showed every UNASSIGNED lead to every agent, which is how sales agents
    were seeing leads that were not theirs (client 09-16). Stock crm loads
    the rule with noupdate="1", so an XML override of `active` is ignored —
    it has to be done here. Mudon agents keep their own "own leads only"
    rule, and every user in this database is a Mudon user, so nothing that
    should see leads loses access. Idempotent.
    """
    cr.execute("""
        UPDATE ir_rule SET active = false
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 'crm' AND name = 'crm_rule_personal_lead'
        ) AND active = true
    """)
