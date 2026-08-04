"""Stage colours for the form stage bar, generated from the stage records.

Client comment 10 wants the stage bar on the form to carry the same colours
as the kanban bands. Two earlier attempts failed for reasons worth recording:

1. ``:nth-of-type`` on the buttons. Unusable: web.StatusBarField also renders
   two dropdown toggles that share the ``o_arrow_button`` class, so the
   indices slide, and which stages collapse into "..." changes with the
   window width.

2. Stamping ``data-mudon-kind`` from a JS service. The markup and the CSS
   were both correct, but the service loads the stage map asynchronously and
   the form renders first, so the attribute came out empty and OWL dropped
   it. Nothing re-renders when a module-level map fills in later.

Odoo already emits ``data-value="<stage id>"`` on every stage button, with no
help from us. So the colours are emitted here as a tiny stylesheet keyed on
those ids, built from the stages themselves. No JavaScript, no async race, no
template patching, and it cannot drift from the kanban because both read the
same palette.
"""
from odoo import http
from odoo.http import request

# Same values as static/src/scss/mudon_kanban.scss. Keep the two in step.
MUDON_STAGE_COLORS = {
    "new_lead": ("#dc3545", "#ffffff"),
    "qualified": ("#fd7e14", "#ffffff"),
    "offer_sent": ("#ffc107", "#212529"),   # yellow needs dark text
    "meeting": ("#8bc34a", "#ffffff"),
    "eoi": ("#4caf50", "#ffffff"),
    "won": ("#198754", "#ffffff"),
    "lost": ("#000000", "#ffffff"),
}

_HEADER = "/* Mudon stage bar, generated per stage id by controllers/stage_css.py */"

_STAGE_RULE = """
.o_form_statusbar .o_statusbar_status button.o_arrow_button[data-value="%(id)s"] {
    background-color: %(bg)s !important;
    border-color: %(bg)s !important;
    color: %(fg)s !important;
    font-weight: 600;
    opacity: .5;
}
.o_form_statusbar .o_statusbar_status button.o_arrow_button[data-value="%(id)s"]:hover {
    opacity: .8;
}
.o_form_statusbar .o_statusbar_status button.o_arrow_button[data-value="%(id)s"].o_arrow_button_current,
.o_form_statusbar .o_statusbar_status button.o_arrow_button[data-value="%(id)s"][aria-current="step"] {
    opacity: 1;
    font-weight: 700;
    box-shadow: inset 0 0 0 2px rgba(255, 255, 255, .7);
}
"""


class MudonStageCss(http.Controller):

    @http.route("/mudon/stage_colors.css", type="http", auth="user")
    def mudon_stage_colors(self, **kwargs):
        """One CSS rule per Mudon stage, keyed on its database id."""
        css = [_HEADER]
        stages = request.env["crm.stage"].sudo().search(
            [("mudon_stage_kind", "!=", False)])
        for stage in stages:
            colors = MUDON_STAGE_COLORS.get(stage.mudon_stage_kind)
            if not colors:
                continue
            bg, fg = colors
            css.append(_STAGE_RULE % {"id": stage.id, "bg": bg, "fg": fg})
        headers = [
            ("Content-Type", "text/css; charset=utf-8"),
            # Stages are configuration data and change rarely, but a new
            # pipeline must show up without a hard refresh.
            ("Cache-Control", "no-cache, max-age=0"),
        ]
        return request.make_response("\n".join(css), headers=headers)
