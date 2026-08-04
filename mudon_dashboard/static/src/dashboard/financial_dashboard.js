/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";
import { download } from "@web/core/network/download";
import {
    Component, useState, onWillStart, onMounted, onWillUnmount, useRef, useEffect,
} from "@odoo/owl";

const NAVY = "#1b2b44";   // Won
const GOLD = "#b4761a";   // Invoiced
const GREEN = "#1e7a46";  // Collected

class MudonFinancialDashboard extends Component {
    static template = "mudon_dashboard.FinancialDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.chart = null;
        this.trendRef = useRef("revenue_trend");
        this.state = useState({
            data: null,
            loading: true,
            error: false,
            pipeline: "all",
            period: "all_time",
            basis: "won",
            source: "crm",
            // extra filters
            agent_id: "",
            source_id: "",
            nationality_id: "",
            country_prefix: "",
            date_from: "",
            date_to: "",
            options: { agents: [], sources: [], nationalities: [], countries: [], stages: [] },
            // deals table client-side sort
            sortKey: "closed",
            sortDir: "desc",
            renderKey: 0,
            // Currency display mode — Abdul 07-06 (item 1b).
            // "native" = show whatever the pipeline currency is (default);
            // "usd"    = force everything to USD at a fixed AED→USD rate.
            currencyMode: "native",
        });
        // Fixed peg per Abdul's instruction. Not read from res.currency
        // because he wanted a stable review rate independent of live FX.
        this.AED_TO_USD = 3.67;

        onWillStart(async () => {
            try {
                await loadJS("/web/static/lib/Chart/Chart.js");
            } catch (e) {
                // chart just won't render; KPIs + tables still work
            }
            await this.loadOptions();
            await this.load();
        });
        useEffect(() => this.renderChart(), () => [this.state.renderKey]);
        onMounted(() => this.renderChart());
        onWillUnmount(() => this.destroyChart());
    }

    buildFilters() {
        return {
            agent_id: this.state.agent_id || null,
            source_id: this.state.source_id || null,
            nationality_id: this.state.nationality_id || null,
            country_prefix: this.state.country_prefix || null,
            date_from: this.state.date_from || null,
            date_to: this.state.date_to || null,
        };
    }

    async loadOptions() {
        try {
            this.state.options = await this.orm.call(
                "mudon.dashboard", "get_filter_options", [this.state.pipeline]);
        } catch (e) {
            // keep previous options
        }
    }

    async load() {
        this.state.loading = true;
        this.state.error = false;
        try {
            const data = await this.orm.call(
                "mudon.dashboard", "get_financial_data",
                [this.state.pipeline, this.state.period, this.state.basis,
                 this.state.source, this.buildFilters()]);
            this.state.data = data;
            this.state.renderKey++;
        } catch (e) {
            this.state.error = (e && e.data && e.data.message) || (e && e.message) || String(e);
        } finally {
            this.state.loading = false;
        }
    }

    async setPipeline(ev) { this.state.pipeline = ev.target.value; await this.loadOptions(); await this.load(); }
    async setPeriod(ev) { this.state.period = ev.target.value; await this.load(); }
    async setBasis(b) { this.state.basis = b; await this.load(); }
    async setSource(s) { this.state.source = s; await this.load(); }
    async setFilter(key, ev) { this.state[key] = ev.target.value; await this.load(); }
    async setDate(key, ev) { this.state[key] = ev.target.value; await this.load(); }
    async clearFilters() {
        Object.assign(this.state, {
            agent_id: "", source_id: "", nationality_id: "",
            country_prefix: "", date_from: "", date_to: "",
        });
        await this.load();
    }
    async refresh() { await this.load(); }

    // ── drill-through: KPI → the won deals behind it; deal row → its lead ─
    openLeads(res) {
        if (!res) { return; }
        const ctx = { create: false };
        if (res.team_id) { ctx.default_team_id = res.team_id; }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: res.name || "Deals",
            res_model: "crm.lead",
            domain: res.domain || [],
            views: [[false, "list"], [false, "form"]],
            target: "current",
            context: ctx,
        });
    }

    async openDrill(block, key = null) {
        if (!block) { return; }
        try {
            const res = await this.orm.call(
                "mudon.dashboard", "get_drill_domain",
                [block, key, this.state.pipeline, this.state.period,
                 this.state.basis, this.buildFilters()]);
            this.openLeads(res);
        } catch (e) {
            // best-effort
        }
    }

    openDeal(id) {
        if (!id) { return; }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "crm.lead",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async downloadReport() {
        try {
            await download({
                url: "/mudon/dashboard/export",
                data: Object.assign(
                    { kind: "financial", pipeline: this.state.pipeline,
                      period: this.state.period, basis: this.state.basis,
                      source: this.state.source },
                    this._filterParams()),
            });
        } catch (e) {
            // download() surfaces server errors as a dialog
        }
    }

    _filterParams() {
        const f = this.buildFilters();
        const out = {};
        for (const k in f) { out[k] = f[k] == null ? "" : f[k]; }
        return out;
    }

    // QWeb has no String() global; compare option ids as strings via a method
    optSel(a, b) { return "" + a === "" + b; }

    // ── deals detail: client-side sort ──────────────────────────────────
    get dealRows() {
        const d = this.state.data;
        const deals = (d && d.tables && d.tables.deals && d.tables.deals.rows) || [];
        const k = this.state.sortKey;
        const dir = this.state.sortDir === "asc" ? 1 : -1;
        const numeric = ["expected_revenue", "commission", "invoiced", "collected"].includes(k);
        return [...deals].sort((a, b) => {
            let x = a[k], y = b[k];
            if (numeric) { return ((+x || 0) - (+y || 0)) * dir; }
            return ((x || "").toString()).localeCompare((y || "").toString()) * dir;
        });
    }
    sortDeals(key) {
        if (this.state.sortKey === key) {
            this.state.sortDir = this.state.sortDir === "asc" ? "desc" : "asc";
        } else { this.state.sortKey = key; this.state.sortDir = "asc"; }
    }
    sortCaret(key) {
        if (this.state.sortKey !== key) { return ""; }
        return this.state.sortDir === "asc" ? " ▲" : " ▼";
    }
    fmtDate(s) { return s || "—"; }

    // Convert a native amount to the currently-selected display
    // currency + return { value, sym, position }. Native → USD when
    // the toggle is USD and the native is AED (divide by 3.67); native
    // → AED not currently offered (Abdul asked only for USD toggle).
    _mudonDisplay(v) {
        const m = (this.state.data && this.state.data.meta) || {};
        const nativeSym = m.currency || "";
        const nativePos = m.currency_position || "before";
        // Drive this off an explicit backend flag rather than sniffing the
        // symbol: the board used to receive the COMPANY currency (always
        // USD), so this branch could never fire and the toggle did nothing
        // (comment 4).
        const isAed = m.currency_is_aed || nativeSym === "AED" || nativeSym === "د.إ";
        if (this.state.currencyMode === "usd" && isAed) {
            return {
                value: v / (m.aed_per_usd || this.AED_TO_USD),
                sym: "$",
                position: "before",
            };
        }
        return { value: v, sym: nativeSym, position: nativePos };
    }

    fmtMoney(v) {
        if (v == null) { return "—"; }
        const d = this._mudonDisplay(v);
        let n;
        const a = Math.abs(d.value);
        if (a >= 1000000) { n = (d.value / 1000000).toFixed(2).replace(/\.?0+$/, "") + "M"; }
        else if (a >= 1000) { n = Math.round(d.value / 1000) + "K"; }
        else { n = new Intl.NumberFormat().format(Math.round(d.value)); }
        return d.position === "after" ? `${n} ${d.sym}` : `${d.sym} ${n}`;
    }

    fmtFull(v) {
        if (v == null) { return "—"; }
        const d = this._mudonDisplay(v);
        const n = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(Math.round(d.value));
        return d.position === "after" ? `${n} ${d.sym}` : `${d.sym} ${n}`;
    }

    setCurrencyMode(mode) {
        this.state.currencyMode = mode;
        this.state.renderKey++;  // trigger chart re-render with new axis labels
    }

    get kpiCards() {
        const d = this.state.data;
        if (!d) { return []; }
        const k = d.kpis || {};
        return [
            { label: "Won (commission)", value: this.fmtMoney(k.won), color: "navy", block: "fin_won" },
            { label: "Invoiced", value: this.fmtMoney(k.invoiced), color: "gold", block: "fin_invoiced" },
            { label: "Collected", value: this.fmtMoney(k.collected), color: "green", block: "fin_collected" },
            { label: "Unbilled (backlog)", value: this.fmtMoney(k.unbilled), color: (k.unbilled ? "amber" : "grey"), block: "fin_unbilled" },
            { label: "Yet to collect", value: this.fmtMoney(k.to_collect), color: (k.to_collect ? "amber" : "grey"), block: "fin_to_collect" },
            {
                label: "Collection rate",
                value: (k.collection_rate == null ? "—" : k.collection_rate + "%"),
                color: "blue", block: "fin_collected",
            },
        ];
    }

    // The four breakdown tables, described declaratively for the template.
    get tables() {
        const d = this.state.data;
        if (!d || !d.tables) { return []; }
        const t = d.tables;
        return [
            { key: "agents", title: "By Agent", head: "Agent", data: t.agents },
            { key: "countries", title: "By Client Country", head: "Country", data: t.countries },
            { key: "nationalities", title: "By Nationality", head: "Nationality", data: t.nationalities },
            { key: "sources", title: "By Lead Source", head: "Source", data: t.sources },
        ];
    }

    destroyChart() {
        if (this.chart) {
            try { this.chart.destroy(); } catch (e) { /* noop */ }
            this.chart = null;
        }
    }

    renderChart() {
        const d = this.state.data;
        if (!d || !window.Chart) { return; }
        const c = d.charts && d.charts.revenue_trend;
        this.destroyChart();
        if (!c || !this.trendRef.el) { return; }
        this.chart = new window.Chart(this.trendRef.el, {
            type: "bar",
            data: {
                labels: c.labels,
                datasets: [
                    { label: "Won " + c.year, data: c.won, backgroundColor: NAVY, borderRadius: 3 },
                    { label: "Invoiced " + c.year, data: c.invoiced, backgroundColor: GOLD, borderRadius: 3 },
                    { label: "Collected " + c.year, data: c.collected, backgroundColor: GREEN, borderRadius: 3 },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: "top", labels: { boxWidth: 12 } } },
                scales: { y: { beginAtZero: true } },
            },
        });
    }
}

registry.category("actions").add("mudon_financial_dashboard", MudonFinancialDashboard);
