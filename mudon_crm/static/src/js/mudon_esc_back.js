/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Comment 25 — "While inside the card, can I go back to pipeline dashboard
 * if I click ESC?"
 *
 * Yes. This service listens for Escape while a Mudon lead form is open and
 * walks the breadcrumb back to the pipeline, which is the same thing the
 * "← Back to Pipeline" button does.
 *
 * It is deliberately conservative — Escape keeps its normal meaning in every
 * situation where the user is mid-interaction, so this never steals the key
 * from Odoo. It bails out when:
 *
 *   - a dialog / modal is open              (Escape must close it)
 *   - an autocomplete dropdown is open      (Escape must close it)
 *   - focus is in an input, textarea or
 *     any contenteditable                   (Escape must revert the field)
 *   - the record has unsaved changes        (never discard silently)
 *   - the open form is not a Mudon lead     (marker div absent)
 *   - there is no breadcrumb to go back to  (nothing above us)
 */
const EDITABLE = ["INPUT", "TEXTAREA", "SELECT"];

function shouldIgnore() {
    // A dialog owns Escape.
    if (document.querySelector(".modal.show, .o_dialog, .modal-backdrop")) {
        return true;
    }
    // An open autocomplete / dropdown owns Escape.
    if (document.querySelector(".o-autocomplete--dropdown-menu, .dropdown-menu.show")) {
        return true;
    }
    const el = document.activeElement;
    if (el) {
        if (EDITABLE.includes(el.tagName)) {
            return true;
        }
        if (el.isContentEditable) {
            return true;
        }
    }
    // Unsaved edits: let Odoo's own discard handling deal with Escape.
    if (document.querySelector(".o_form_status_indicator_buttons:not(.invisible)")) {
        return true;
    }
    return false;
}

export const mudonEscBackService = {
    dependencies: [],
    start() {
        const onKeyDown = (ev) => {
            if (ev.key !== "Escape" || ev.defaultPrevented) {
                return;
            }
            // Only on a saved Mudon lead form — the marker div renders
            // exclusively for records whose pipeline is Turkey or UAE.
            const form = document.querySelector(
                ".o_form_view .mudon_form_marker");
            if (!form || shouldIgnore()) {
                return;
            }
            // Walk back one level via the breadcrumb, which lands on the
            // kanban the user opened the card from.
            const crumbs = document.querySelectorAll(
                ".o_breadcrumb .breadcrumb-item a, .o_breadcrumb a.o_back_button");
            if (!crumbs.length) {
                return;
            }
            ev.preventDefault();
            ev.stopPropagation();
            crumbs[crumbs.length - 1].click();
        };
        // Capture phase so we see the key before the form's own handler,
        // but every guard above has already run by then.
        document.addEventListener("keydown", onKeyDown, true);
        return {};
    },
};

registry.category("services").add("mudon_esc_back", mudonEscBackService);
