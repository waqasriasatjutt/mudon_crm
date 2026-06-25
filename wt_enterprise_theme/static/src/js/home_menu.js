/* Copyright Way4Tech — based on web_enterprise HomeMenu, subscription checks removed */
import { hasTouch } from "@web/core/browser/feature_detection";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { useSortable } from "@web/core/utils/sortable_owl";

import {
    Component,
    useExternalListener,
    onMounted,
    onPatched,
    onWillUpdateProps,
    useState,
    useRef,
} from "@odoo/owl";

export class HomeMenu extends Component {
    static template = "wt_enterprise_theme.HomeMenu";
    static props = {
        apps: {
            type: Array,
            element: {
                type: Object,
                shape: {
                    actionID: Number,
                    href: String,
                    appID: Number,
                    id: Number,
                    label: String,
                    parents: String,
                    webIcon: {
                        type: [
                            Boolean,
                            String,
                            {
                                type: Object,
                                optional: 1,
                                shape: {
                                    iconClass: String,
                                    color: String,
                                    backgroundColor: String,
                                },
                            },
                        ],
                        optional: true,
                    },
                    webIconData: { type: String, optional: 1 },
                    xmlid: String,
                },
            },
        },
        reorderApps: { type: Function },
    };

    setup() {
        this.menus = useService("menu");
        this.homeMenuService = useService("home_menu");
        this.ui = useService("ui");
        this.state = useState({ focusedIndex: null });
        this.inputRef = useRef("input");
        this.rootRef = useRef("root");

        if (!this.env.isSmall) {
            this._registerHotkeys();
        }

        useSortable({
            enable: () => true,
            ref: this.rootRef,
            elements: ".o_draggable",
            cursor: "move",
            delay: 500,
            tolerance: 10,
            onWillStartDrag: ({ element, addClass }) =>
                addClass(element.children[0], "o_dragged_app"),
            onDrop: (params) => this._sortAppDrop(params),
        });

        onWillUpdateProps(() => {
            this.state.focusedIndex = null;
        });

        onMounted(() => {
            if (!hasTouch()) {
                this._focusInput();
            }
        });

        onPatched(() => {
            if (this.state.focusedIndex !== null && !this.env.isSmall) {
                const selectedItem = document.querySelector(
                    ".o_home_menu .o_menuitem.o_focused"
                );
                if (selectedItem) {
                    selectedItem.scrollIntoView({ block: "center" });
                }
            }
        });
    }

    get displayedApps() {
        return this.props.apps;
    }

    get maxIconNumber() {
        const w = window.innerWidth;
        if (w < 576) return 3;
        if (w < 768) return 4;
        return 6;
    }

    _openMenu(menu) {
        return this.menus.selectMenu(menu);
    }

    _updateFocusedIndex(cmd) {
        const nbrApps = this.displayedApps.length;
        const lastIndex = nbrApps - 1;
        const focusedIndex = this.state.focusedIndex;
        if (lastIndex < 0) return;
        if (focusedIndex === null) {
            this.state.focusedIndex = 0;
            return;
        }
        const lineNumber = Math.ceil(nbrApps / this.maxIconNumber);
        const currentLine = Math.ceil((focusedIndex + 1) / this.maxIconNumber);
        let newIndex;
        switch (cmd) {
            case "previousElem":
                newIndex = focusedIndex - 1;
                break;
            case "nextElem":
                newIndex = focusedIndex + 1;
                break;
            case "previousColumn":
                newIndex = focusedIndex % this.maxIconNumber
                    ? focusedIndex - 1
                    : focusedIndex + Math.min(lastIndex - focusedIndex, this.maxIconNumber - 1);
                break;
            case "nextColumn":
                newIndex =
                    focusedIndex === lastIndex ||
                    (focusedIndex + 1) % this.maxIconNumber === 0
                        ? (currentLine - 1) * this.maxIconNumber
                        : focusedIndex + 1;
                break;
            case "previousLine":
                newIndex =
                    currentLine === 1
                        ? Math.min(
                              focusedIndex + (lineNumber - 1) * this.maxIconNumber,
                              lastIndex
                          )
                        : focusedIndex - this.maxIconNumber;
                break;
            case "nextLine":
                newIndex =
                    currentLine === lineNumber
                        ? focusedIndex % this.maxIconNumber
                        : focusedIndex + Math.min(this.maxIconNumber, lastIndex - focusedIndex);
                break;
        }
        if (newIndex < 0) newIndex = lastIndex;
        else if (newIndex > lastIndex) newIndex = 0;
        this.state.focusedIndex = newIndex;
    }

    _focusInput() {
        if (!this.env.isSmall && this.inputRef.el) {
            this.inputRef.el.focus({ preventScroll: true });
        }
    }

    _sortAppDrop({ element, previous }) {
        const order = this.props.apps.map((app) => app.xmlid);
        const elementId = element.children[0].dataset.menuXmlid;
        const elementIndex = order.indexOf(elementId);
        order.splice(elementIndex, 1);
        if (previous) {
            const prevIndex = order.indexOf(previous.children[0].dataset.menuXmlid);
            order.splice(prevIndex + 1, 0, elementId);
        } else {
            order.splice(0, 0, elementId);
        }
        this.props.reorderApps(order);
        user.setUserSettings("homemenu_config", JSON.stringify(order));
    }

    _onAppClick(app) {
        this._openMenu(app);
    }

    _registerHotkeys() {
        const hotkeys = [
            ["ArrowDown", () => this._updateFocusedIndex("nextLine")],
            ["ArrowRight", () => this._updateFocusedIndex("nextColumn")],
            ["ArrowUp", () => this._updateFocusedIndex("previousLine")],
            ["ArrowLeft", () => this._updateFocusedIndex("previousColumn")],
            ["Tab", () => this._updateFocusedIndex("nextElem")],
            ["shift+Tab", () => this._updateFocusedIndex("previousElem")],
            [
                "Enter",
                () => {
                    const menu = this.displayedApps[this.state.focusedIndex];
                    if (menu) this._openMenu(menu);
                },
            ],
            ["Escape", () => this.homeMenuService.toggle(false)],
        ];
        hotkeys.forEach((hotkey) => useHotkey(...hotkey, { allowRepeat: true }));
        useExternalListener(window, "keydown", this._onKeydownFocusInput.bind(this));
    }

    _onKeydownFocusInput() {
        if (
            document.activeElement !== this.inputRef.el &&
            this.ui.activeElement === document &&
            !["TEXTAREA", "INPUT"].includes(document.activeElement.tagName)
        ) {
            this._focusInput();
        }
    }
}
