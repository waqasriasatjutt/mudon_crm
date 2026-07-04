/** @odoo-module **/

import { registry } from "@web/core/registry";
import { RPCError } from "@web/core/network/rpc";
import { UncaughtPromiseError } from "@web/core/errors/error_service";

/**
 * Open the Mudon quick-fill wizard DIRECTLY when a stage drag needs
 * missing fields, skipping the generic "Odoo Warning" RedirectWarning
 * dialog (client feedback: no warning step, straight to the input popup).
 *
 * Scoped tightly: only a RedirectWarning whose action targets the Mudon
 * quick-fill wizard is intercepted — every other RedirectWarning falls
 * through to Odoo's standard dialog untouched. Runs before the core
 * rpcErrorHandler (sequence 97) so the dialog never renders.
 */
function mudonAutoOpenWizard(env, error, originalError) {
    if (!(error instanceof UncaughtPromiseError)) {
        return false;
    }
    if (!(originalError instanceof RPCError)) {
        return false;
    }
    if (originalError.exceptionName !== "odoo.exceptions.RedirectWarning") {
        return false;
    }
    const args = originalError.data && originalError.data.arguments;
    const action = args && args[1];
    if (!action || action.res_model !== "mudon.quick.fill.wizard") {
        return false;
    }
    // Our wizard — suppress the warning dialog and open it straight away.
    if (error.unhandledRejectionEvent) {
        error.unhandledRejectionEvent.preventDefault();
    }
    const options = { forceLeave: true };
    if (args[3]) {
        options.additionalContext = args[3];
    }
    env.services.action.doAction(action, options);
    return true;
}

registry
    .category("error_handlers")
    .add("mudonAutoOpenWizard", mudonAutoOpenWizard, { sequence: 1 });
