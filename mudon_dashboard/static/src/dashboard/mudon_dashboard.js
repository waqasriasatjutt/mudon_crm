/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";
import { download } from "@web/core/network/download";
import {
    Component, useState, onWillStart, onMounted, onWillUnmount, useRef, useEffect,
} from "@odoo/owl";

const NAVY = "#1b2b44";
const GREY = "#b9c0c4";

class MudonManagementDashboard extends Component {
    static template = "mudon_dashboard.ManagementDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.chart = null;
        this.trendRef = useRef("commission_trend");
        this.state = useState({
            data: null,
            loading: true,
            error: false,
            pipeline: "all",
            period: "this_month",
            // Comment 4 - the same USD toggle the financial board has, so a
            // manager can read the UAE board in AED or in USD at the fixed
            // peg. "native" shows the pipeline's own currency.
            currencyMode: "native",
            basis: "pipeline",
            // extra filters
            agent_id: "",
            source_id: "",
            nationality_id: "",
            country_prefix: "",
            stage_kind: "",
            date_from: "",
            date_to: "",
            options: { agents: [], sources: [], nationalities: [], countries: [], stages: [] },
            renderKey: 0,
        });

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
            stage_kind: this.state.stage_kind || null,
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
                "mudon.dashboard", "get_management_data",
                [this.state.pipeline, this.state.period, this.state.basis, this.buildFilters()]);
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
    async setFilter(key, ev) { this.state[key] = ev.target.value; await this.load(); }
    async setDate(key, ev) { this.state[key] = ev.target.value; await this.load(); }
    async clearFilters() {
        Object.assign(this.state, {
            agent_id: "", source_id: "", nationality_id: "",
            country_prefix: "", stage_kind: "", date_from: "", date_to: "",
        });
        await this.load();
    }
    async refresh() { await this.load(); }

    // ── drill-through: click a KPI / table row → the leads behind it ─────
    openLeads(res) {
        if (!res) { return; }
        const ctx = { create: false };
        if (res.team_id) { ctx.default_team_id = res.team_id; }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: res.name || "Leads",
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
            // drill is best-effort; leave the dashboard as-is on error
        }
    }

    async downloadReport() {
        try {
            await download({
                url: "/mudon/dashboard/export",
                data: Object.assign(
                    { kind: "management", pipeline: this.state.pipeline,
                      period: this.state.period, basis: this.state.basis },
                    this._filterParams()),
            });
        } catch (e) {
            // download() surfaces server errors as a dialog; nothing to do
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

    get pipelineLabel() {
        return { all: "All pipelines", uae: "UAE Dubai", turkey: "Turkey" }[this.state.pipeline];
    }

    fmtNum(v) {
        return v == null ? "0" : new Intl.NumberFormat().format(v);
    }

    // Convert a native amount into the selected display currency.
    // The backend now reports the PIPELINE currency (AED on the UAE board,
    // USD on Turkey) instead of the company currency, which is what made
    // every board read "$" in comment 4.
    _mudonDisplay(v) {
        const m = (this.state.data && this.state.data.meta) || {};
        if (this.state.currencyMode === "usd" && m.currency_is_aed) {
            return {
                value: v / (m.aed_per_usd || 3.67),
                sym: "$",
                position: "before",
            };
        }
        return {
            value: v,
            sym: m.currency || "",
            position: m.currency_position || "before",
        };
    }

    setCurrencyMode(mode) {
        this.state.currencyMode = mode;
        this.state.renderKey++;
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

    convClass(v) {
        return v >= 8 ? "good" : (v >= 5 ? "ok" : "low");
    }

    get kpiCards() {
        const d = this.state.data;
        if (!d) { return []; }
        const k = d.kpis || {};
        return [
            { label: "Assigned leads", value: this.fmtNum(k.assigned), color: "navy", block: "assigned" },
            { label: "Not actioned", value: this.fmtNum(k.not_actioned), color: (k.not_actioned ? "amber" : "grey"), block: "not_actioned" },
            { label: "Qualified", value: this.fmtNum(k.qualified), color: "blue", block: "qualified" },
            { label: "Meetings", value: this.fmtNum(k.meetings), color: "teal", block: "meetings" },
            { label: "Won", value: this.fmtNum(k.won), color: "green", block: "won" },
            { label: "Commission", value: this.fmtMoney(k.commission), color: "gold", block: "commission" },
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
        const c = d.charts && d.charts.commission_trend;
        this.destroyChart();
        if (!c || !this.trendRef.el) { return; }
        this.chart = new window.Chart(this.trendRef.el, {
            data: {
                labels: c.labels,
                datasets: [
                    {
                        type: "bar", label: "Commission " + c.year,
                        data: c.this_year, backgroundColor: NAVY, order: 2,
                        borderRadius: 3,
                    },
                    {
                        type: "line", label: "Commission " + (c.year - 1),
                        data: c.last_year, borderColor: GREY, borderDash: [6, 4],
                        backgroundColor: GREY, pointRadius: 3, tension: 0.3, order: 1,
                    },
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

registry.category("actions").add("mudon_management_dashboard", MudonManagementDashboard);
