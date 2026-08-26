from odoo import fields, models


class MudonWaMessage(models.Model):
    """Audit log of every WhatsApp message the CRM sends or receives.

    Doubles as the retry queue: outbound rows in state 'failed' (transient)
    are re-sent by `crm.lead._mudon_cron_wa_retry`. Permanent failures
    (bad number / unapproved template / auth) are marked and left alone.
    """

    _name = "mudon.wa.message"
    _description = "Mudon WhatsApp Message Log"
    _order = "create_date desc"
    _rec_name = "to_number"

    lead_id = fields.Many2one(
        "crm.lead", string="Lead", ondelete="set null", index=True,
    )
    direction = fields.Selection(
        [("out", "Outbound"), ("in", "Inbound")],
        string="Direction", default="out", index=True,
    )
    to_number = fields.Char(string="To / From")
    from_company = fields.Boolean(
        string="From Company Number",
        help="Sent from the company survey number rather than the "
             "default agent/business number.",
    )
    body = fields.Text(string="Message")
    status = fields.Selection(
        [
            ("stub", "Stub (not sent)"),
            ("sent", "Sent"),
            ("delivered", "Delivered"),
            ("read", "Read"),
            ("received", "Received"),
            ("failed", "Failed (will retry)"),
            ("failed_permanent", "Failed (permanent)"),
        ],
        string="Status", default="sent", index=True,
    )
    template_key = fields.Char(
        string="Template Key",
        help="Which registered template this message went out as. Kept so "
             "a retry can re-send it as the SAME approved template — "
             "retrying it as plain text would be dropped by Meta outside "
             "the 24-hour window.",
    )
    template_params = fields.Char(
        string="Template Parameters",
        help="Internal. JSON list of the values substituted into the "
             "template, so a retry reproduces the original message.",
    )
    wamid = fields.Char(string="WhatsApp Message ID", index=True)
    error = fields.Text(string="Error")
    attempts = fields.Integer(string="Attempts", default=1)
