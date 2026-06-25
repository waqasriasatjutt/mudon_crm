/* Copyright Way4Tech — based on web_enterprise homeMenuService, subscription removed */
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { Mutex } from "@web/core/utils/concurrency";
import { useBus, useService } from "@web/core/utils/hooks";
import { computeAppsAndMenuItems, reorderApps } from "@web/webclient/menus/menu_helpers";
import {
    ControllerNotFoundError,
    standardActionServiceProps,
} from "@web/webclient/actions/action_service";
import { HomeMenu } from "./home_menu";

import { Component, onMounted, onWillUnmount, reactive, xml } from "@odoo/owl";

// useBus and useService are used inside HomeMenuAction.setup() (OWL component context)

export const homeMenuService = {
    dependencies: ["action"],
    start(env) {
        const state = reactive({
            hasHomeMenu: false,
            hasBackgroundAction: false,
            toggle,
        });

        const mutex = new Mutex();

        class HomeMenuAction extends Component {
            static components = { HomeMenu };
            static target = "current";
            static props = { ...standardActionServiceProps };
            static template = xml`<HomeMenu t-props="homeMenuProps"/>`;
            static displayName = _t("Home");

            setup() {
                this.menus = useService("menu");
                onMounted(async () => {
                    state.hasHomeMenu = true;
                    state.hasBackgroundAction =
                        (this.env.config.breadcrumbs || []).length > 0;
                    this.env.bus.trigger("HOME-MENU:TOGGLED");
                });
                onWillUnmount(() => {
                    state.hasHomeMenu = false;
                    state.hasBackgroundAction = false;
                    this.env.bus.trigger("HOME-MENU:TOGGLED");
                });
                useBus(this.env.bus, "MENUS:APP-CHANGED", () => this.render());
            }

            get homeMenuProps() {
                const homemenuConfig = JSON.parse(user.settings?.homemenu_config || "null");
                const apps = reactive(
                    computeAppsAndMenuItems(this.menus.getMenuAsTree("root")).apps
                );
                if (homemenuConfig) {
                    reorderApps(apps, homemenuConfig);
                }
                return {
                    apps,
                    reorderApps: (order) => reorderApps(apps, order),
                };
            }
        }

        registry.category("actions").add("menu", HomeMenuAction);

        env.bus.addEventListener("HOME-MENU:TOGGLED", () => {
            document.body.classList.toggle("o_home_menu_background", state.hasHomeMenu);
        });

        async function toggle(show) {
            return mutex.exec(async () => {
                show = show === undefined ? !state.hasHomeMenu : Boolean(show);
                if (show !== state.hasHomeMenu) {
                    if (show) {
                        await env.services.action.doAction("menu");
                    } else {
                        try {
                            await env.services.action.restore();
                        } catch (err) {
                            if (!(err instanceof ControllerNotFoundError)) {
                                throw err;
                            }
                        }
                    }
                }
                return new Promise((r) => setTimeout(r));
            });
        }

        return state;
    },
};

registry.category("services").add("home_menu", homeMenuService);
