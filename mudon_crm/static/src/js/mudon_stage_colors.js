/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";
import { StatusBarField } from "@web/views/fields/statusbar/statusbar_field";

/**
 * Client comment 10 - "in stages when opens form you added colors but its not
 * same like stage colors in kanban view".
 *
 * The first attempt coloured the stage bar with :nth-of-type(), assuming the
 * Nth button was the Nth stage. It is not. web.StatusBarField renders the
 * visible stages from `items.inline`, but it also renders TWO dropdown toggle
 * buttons that carry the same `o_arrow_button` class for the stages that do
 * not fit ("..."). Those toggles are counted by :nth-of-type, so the colours
 * slid onto the wrong stages, and which stages collapse changes with the
 * window width. Position is simply not a reliable key here.
 *
 * The reliable key is the stage's own `mudon_stage_kind`. The statusbar
 * preloads its stage records and maps them in getAllItems(), so this hooks
 * that map and stamps the kind onto each item; the template inherit then
 * emits it as `data-mudon-kind`, and the stylesheet colours by that. The
 * result is independent of order, of how many stages collapse, and of the
 * stage ids in any given database.
 */

// Stage id -> mudon_stage_kind, loaded once at web-client boot.
const STAGE_KINDS = {};

registry.category("services").add("mudon_stage_kinds", {
    dependencies: ["orm"],
    async start(env, { orm }) {
        try {
            const rows = await orm.searchRead(
                "crm.stage",
                [["mudon_stage_kind", "!=", false]],
                ["id", "mudon_stage_kind"],
                { context: { active_test: false } }
            );
            for (const row of rows) {
                STAGE_KINDS[row.id] = row.mudon_stage_kind;
            }
        } catch {
            // Not a Mudon database, or the field is absent. Leave the map
            // empty so the stage bar simply keeps Odoo's default styling.
        }
        return {};
    },
});

patch(StatusBarField.prototype, {
    getAllItems() {
        const items = super.getAllItems(...arguments);
        if (this.props.name !== "stage_id") {
            return items;
        }
        for (const item of items) {
            item.mudonKind = STAGE_KINDS[item.value] || false;
        }
        return items;
    },
});
