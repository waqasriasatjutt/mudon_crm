/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Domain } from "@web/core/domain";
import { DomainSelectorDialog } from "@web/core/domain_selector_dialog/domain_selector_dialog";
import { RecordAutocomplete } from "@web/core/record_selectors/record_autocomplete";
import { onWillDestroy } from "@odoo/owl";

/**
 * Client comment 7 - "when filtering stage while in Dubai pipeline, it shows
 * all the item of both pipelines".
 *
 * Opening Filters > Add Custom Filter > Stage lists all 14 stages, seven of
 * which belong to the other pipeline. Scoping this server-side is not
 * possible: the picker runs with an empty context, so the stage model has no
 * way to know which pipeline the user is in.
 *
 * Tracing the chain in Odoo 19 web:
 *
 *   search_model.spawnCustomFilterDialog()
 *       passes `context: this.globalContext`  <- default_team_id IS here
 *   DomainSelectorDialog
 *       uses props.context ONLY to evaluate the domain
 *   DomainSelector -> TreeEditor -> tree_editor_value_editors
 *       extractProps() sends resModel/domain/update/resIds, and no context
 *   RecordAutocomplete
 *       forwards props.context to name_search, but it arrives empty
 *
 * So the context exists at the top of the chain and is dropped before the
 * bottom. Rather than thread a new prop through four core components, the
 * dialog stashes its context on the way in and the picker reads it back.
 *
 * RecordAutocomplete.getDomain() is the single choke point: both the inline
 * autocomplete and the "Search: Stage" dialog (onSearchMore) call it, so one
 * override covers both. The override is inert unless the model really is
 * crm.stage and a pipeline is actually known, which keeps every other custom
 * filter in the database behaving exactly as before.
 */

// Context of the domain dialog that is currently open, or {} when none is.
let activeDomainContext = {};

patch(DomainSelectorDialog.prototype, {
    setup() {
        super.setup(...arguments);
        activeDomainContext = this.props.context || {};
        // Clear on close so a stale pipeline can never leak into an
        // unrelated stage picker opened later.
        onWillDestroy(() => {
            activeDomainContext = {};
        });
    },
});

patch(RecordAutocomplete.prototype, {
    getDomain() {
        const base = super.getDomain(...arguments);
        if (this.props.resModel !== "crm.stage") {
            return base;
        }
        const teamId = activeDomainContext.default_team_id;
        if (!teamId) {
            // All Mudon Leads and other unscoped views: show every stage,
            // which is the correct behaviour there.
            return base;
        }
        return Domain.and([base, [["team_ids", "=", teamId]]]).toList();
    },
});
