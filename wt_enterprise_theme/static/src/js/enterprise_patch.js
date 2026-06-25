/* Copyright Way4Tech
 * Patches WebClient and NavBar to use the Enterprise-style home menu.
 */
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { WebClient } from "@web/webclient/webclient";
import { NavBar } from "@web/webclient/navbar/navbar";

// ── WebClient patch — show home menu on startup ────────────────────────────
patch(WebClient.prototype, {
    setup() {
        super.setup();
        this.hm = useService("home_menu");
    },

    _loadDefaultApp() {
        return this.hm.toggle(true);
    },
});

// ── NavBar patch ──────────────────────────────────────────────────────────
// 1. Expose hm so the XML template can call hm.toggle() on desktop.
// 2. Override _openAppMenuSidebar: on mobile, if the home-menu grid is visible,
//    tapping the hamburger should close it (go back to app) rather than trying
//    to open the sub-menu sidebar on top of the home menu.
patch(NavBar.prototype, {
    setup() {
        super.setup();
        this.hm = useService("home_menu");
    },

    _openAppMenuSidebar() {
        if (this.hm.hasHomeMenu) {
            // Home menu grid is open — close it to reveal the app
            this.hm.toggle(false);
        } else {
            // Normal behaviour: open/close the sub-menu sidebar
            super._openAppMenuSidebar();
        }
    },
});
