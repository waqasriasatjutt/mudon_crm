# -*- coding: utf-8 -*-
"""Data provider for the Mudon Management Dashboard.

One @api.model entry point, ``get_management_data(pipeline, period, basis)``,
returns a single JSON-serialisable dict the OWL client renders. Everything is
computed in Python (search + aggregate) so it stays correct across Odoo point
releases, and every block is wrapped so one failing query can't blank the
whole dashboard.

    pipeline : 'all' | 'turkey' | 'uae'
    period   : 'this_month' | 'last_month' | 'this_quarter' | 'this_year'
               | 'last_year'
    basis    : 'pipeline'  -> group/filter by lead creation date
               'close'     -> group/filter by close (won) date
"""
import calendar
from datetime import date, datetime, time

from odoo import api, fields, models

# Funnel order — a lead that reached a later stage also passed the earlier
# ones, so "reached qualified" == current stage index >= qualified index.
STAGE_INDEX = {
    "new_lead": 0, "qualified": 1, "offer_sent": 2,
    "meeting": 3, "eoi": 4, "won": 5,
}

# Dialing-prefix -> country label for the "by client country" table.
COUNTRY_BY_PREFIX = [
    ("966", "Saudi Arabia (+966)"), ("971", "UAE (+971)"),
    ("965", "Kuwait (+965)"), ("974", "Qatar (+974)"),
    ("968", "Oman (+968)"), ("973", "Bahrain (+973)"),
    ("964", "Iraq (+964)"), ("962", "Jordan (+962)"),
    ("20", "Egypt (+20)"), ("90", "Turkey (+90)"),
    ("1", "USA/Canada (+1)"), ("44", "UK (+44)"),
]


class MudonDashboard(models.TransientModel):
    _name = "mudon.dashboard"
    _description = "Mudon — Management Dashboard data provider"

    # ── period helpers ──────────────────────────────────────────────────
    @api.model
    def _last_day(self, y, m):
        return date(y, m, calendar.monthrange(y, m)[1])

    @api.model
    def _period_range(self, period):
        today = fields.Date.context_today(self)
        y, m = today.year, today.month
        if period == "last_month":
            pm_year, pm = (y, m - 1) if m > 1 else (y - 1, 12)
            return date(pm_year, pm, 1), self._last_day(pm_year, pm)
        if period == "this_quarter":
            qm = 3 * ((m - 1) // 3) + 1
            return date(y, qm, 1), today
        if period == "this_year":
            return date(y, 1, 1), today
        if period == "last_year":
            return date(y - 1, 1, 1), date(y - 1, 12, 31)
        if period == "all_time":
            # Client comment 31 - "No of assigned lead on Dashboard not
            # matching pipeline? 18/15". The board was period-filtered
            # (This Month) while the kanban shows every card, so the two
            # counts could never agree. All Time makes them reconcile.
            return date(2000, 1, 1), today
        # default: this_month
        return date(y, m, 1), today

    # ── filter helpers (shared by both boards + export) ─────────────────
    @staticmethod
    def _as_int(v):
        try:
            return int(v) if v not in (None, "", False) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_to_date(v):
        """Parse a 'YYYY-MM-DD' string, or None on anything malformed.

        The export controller feeds raw query strings straight into filters,
        so a hand-crafted bad date must degrade to the preset — never raise.
        """
        try:
            return fields.Date.to_date(v)
        except (TypeError, ValueError):
            return None

    @api.model
    def _resolve_range(self, period, filters):
        """A custom date_from/date_to on ``filters`` overrides the preset.

        Either side may be supplied alone (the missing side stays open).
        Swapped inputs are corrected. A malformed date falls back to the
        preset window instead of raising.
        """
        filters = filters or {}
        df, dt = filters.get("date_from"), filters.get("date_to")
        if df or dt:
            today = fields.Date.context_today(self)
            d_from = self._safe_to_date(df) if df else date(2000, 1, 1)
            d_to = self._safe_to_date(dt) if dt else today
            if d_from and d_to:
                if d_to < d_from:
                    d_from, d_to = d_to, d_from
                return d_from, d_to
        return self._period_range(period)

    @api.model
    def _apply_filters(self, domain, filters):
        """Fold agent / source / nationality / stage into a domain.

        Returns ``(domain, country_prefix)``. Client-country is derived from
        the phone number (not a stored field), so it is returned for the
        caller to post-filter the searched recordset. Falsy keys are ignored
        → fully backward compatible.
        """
        f = filters or {}
        dom = list(domain)
        aid = self._as_int(f.get("agent_id"))
        sid = self._as_int(f.get("source_id"))
        nid = self._as_int(f.get("nationality_id"))
        if aid:
            dom.append(("user_id", "=", aid))
        if sid:
            dom.append(("mudon_source_id", "=", sid))
        if nid:
            dom.append(("mudon_nationality_id", "=", nid))
        if f.get("stage_kind"):
            dom.append(("mudon_stage_kind_current", "=", f["stage_kind"]))
        return dom, (f.get("country_prefix") or None)

    @api.model
    def _country_prefix_of(self, phone):
        """Dialing prefix bucket for a phone, matching ``_country_label``."""
        norm = self.env["crm.lead"]._mudon_phone_normalize(phone or "")
        if norm.startswith("+"):
            digits = norm[1:]
            for prefix, _label in COUNTRY_BY_PREFIX:
                if digits.startswith(prefix):
                    return prefix
        return "other"

    # ── currency (client comment 4) ─────────────────────────────────────
    # "AED/USD is still not working correctly ... the dashboards are not
    # reflecting correct currency/conversion."
    #
    # Both boards used to report `self.env.company.currency_id`, which is
    # USD for every pipeline — so the UAE board showed $ against AED figures,
    # and the USD toggle had nothing to convert because it only fires when
    # the native currency is AED. The pipeline decides the currency, exactly
    # like crm.lead.mudon_budget_currency_id does on the records themselves.
    AED_PER_USD = 3.67          # fixed peg the client specified

    @api.model
    def _pipeline_currency(self, pipeline):
        """(symbol, position, is_aed) for the selected pipeline.

        UAE bills in AED, Turkey in USD. On the combined "All" board the
        two cannot be added up honestly, so amounts are normalised to USD
        and the UI is told so via ``mixed_currency``.
        """
        if pipeline == "uae":
            aed = self.env.ref("base.AED", raise_if_not_found=False)
            if aed:
                return (aed.symbol or "AED", aed.position or "before", True)
            return ("AED", "before", True)
        usd = self.env.ref("base.USD", raise_if_not_found=False)
        return ((usd.symbol if usd else "$") or "$",
                (usd.position if usd else "before") or "before", False)

    @api.model
    def _currency_meta(self, pipeline):
        symbol, position, is_aed = self._pipeline_currency(pipeline)
        return {
            "currency": symbol,
            "currency_position": position,
            "currency_is_aed": is_aed,
            "aed_per_usd": self.AED_PER_USD,
            # On "All", Turkey (USD) and UAE (AED) figures are converted to
            # a single unit before they are summed - see _to_display_amount.
            "mixed_currency": pipeline == "all",
        }

    @api.model
    def _to_display_amount(self, lead, amount, pipeline):
        """Normalise one lead's amount into the board's display currency.

        Per-pipeline boards already share a currency, so nothing to do. The
        combined board converts AED rows to USD at the fixed peg so the
        totals are not a meaningless mix of two currencies.
        """
        if not amount:
            return 0.0
        if pipeline == "all" and lead.mudon_pipeline_kind == "uae":
            return amount / self.AED_PER_USD
        return amount

    @api.model
    def _period_label(self, period, filters):
        filters = filters or {}
        if filters.get("date_from") or filters.get("date_to"):
            d_from, d_to = self._resolve_range(period, filters)
            return "%s → %s" % (d_from.strftime("%d %b %Y"),
                                d_to.strftime("%d %b %Y"))
        return {
            "this_month": "This Month", "last_month": "Last Month",
            "this_quarter": "This Quarter", "this_year": "This Year",
            "last_year": "Last Year", "all_time": "All Time",
        }.get(period, "This Month")

    @api.model
    def _range_meta(self, period, filters):
        """Meta keys describing the active window (for the UI label)."""
        d_from, d_to = self._resolve_range(period, filters)
        filters = filters or {}
        return {
            "date_from": d_from.isoformat(),
            "date_to": d_to.isoformat(),
            "custom_range": bool(filters.get("date_from")
                                 or filters.get("date_to")),
        }

    # ── option lists for the filter dropdowns ───────────────────────────
    @api.model
    def get_filter_options(self, pipeline="all"):
        """Dropdown options scoped to Mudon opportunities that actually exist
        (so a manager never picks an agent/source/country with zero rows)."""
        Lead = self.env["crm.lead"].sudo()
        domain = [("type", "=", "opportunity"),
                  ("mudon_pipeline_kind", "!=", False)]
        if pipeline in ("turkey", "uae"):
            domain.append(("mudon_pipeline_kind", "=", pipeline))
        agents, sources, nats, prefixes = {}, {}, {}, {}
        for l in Lead.search(domain):
            if l.user_id:
                agents[l.user_id.id] = l.user_id.name
            if l.mudon_source_id:
                sources[l.mudon_source_id.id] = l.mudon_source_id.name
            if l.mudon_nationality_id:
                nats[l.mudon_nationality_id.id] = l.mudon_nationality_id.name
            pref = self._country_prefix_of(l.phone)
            if pref not in prefixes:
                prefixes[pref] = self._country_label(l.phone)

        def lst(d):
            return sorted([{"id": k, "name": v} for k, v in d.items()],
                          key=lambda r: (r["name"] or "").lower())
        return {
            "agents": lst(agents),
            "sources": lst(sources),
            "nationalities": lst(nats),
            "countries": sorted(
                [{"id": k, "name": v} for k, v in prefixes.items()],
                key=lambda r: (r["name"] or "").lower()),
            "stages": [
                {"id": "new_lead", "name": "New"},
                {"id": "qualified", "name": "Qualified"},
                {"id": "offer_sent", "name": "Offer Sent"},
                {"id": "meeting", "name": "Meeting"},
                {"id": "eoi", "name": "EOI"},
                {"id": "won", "name": "Won"},
                {"id": "lost", "name": "Lost"},
            ],
        }

    # ── drill-through: turn a clicked figure into a crm.lead domain ─────
    @api.model
    def get_drill_domain(self, block, key=None, pipeline="all",
                         period="this_month", basis="pipeline", filters=None):
        """Return ``{'domain', 'name', 'team_id'}`` for a clicked dashboard
        element, built from the SAME base + period logic as the data methods
        so the opened list reconciles 1:1 with the headline number.

        ``block`` selects the metric; ``key`` identifies a table row (agent
        id / source id / nationality id / stage kind / phone-prefix)."""
        date_by_basis = {
            "pipeline": "create_date", "close": "date_closed",
            "won": "date_closed", "invoice": "mudon_invoiced_date",
            "payment": "mudon_collected_date",
        }
        d_from, d_to = self._resolve_range(period, filters)
        dt_from = datetime.combine(d_from, time.min)
        dt_to = datetime.combine(d_to, time.max)
        date_field = date_by_basis.get(basis, "create_date")

        base = [("type", "=", "opportunity"),
                ("mudon_pipeline_kind", "!=", False)]
        if pipeline in ("turkey", "uae"):
            base.append(("mudon_pipeline_kind", "=", pipeline))
        base, country_prefix = self._apply_filters(base, filters)
        dom = base + [(date_field, ">=", dt_from), (date_field, "<=", dt_to)]

        WON = ("mudon_stage_kind_current", "=", "won")
        labels = {
            "assigned": "Assigned leads", "not_actioned": "Not actioned",
            "qualified": "Qualified (reached)", "meetings": "Meetings (reached)",
            "won": "Won", "commission": "Won — commission",
            "agent": "Agent leads", "source": "Leads by source",
            "nationality": "Leads by nationality", "country": "Leads by country",
            "stage": "Leads by stage", "fin_won": "Won deals",
            "fin_invoiced": "Invoiced deals", "fin_collected": "Collected deals",
            "fin_unbilled": "Won deals (unbilled backlog)",
            "fin_to_collect": "Won deals (yet to collect)",
        }
        if block == "not_actioned":
            dom.append(("mudon_first_contact_logged", "=", False))
        elif block == "qualified":
            dom.append(("mudon_stage_kind_current", "in",
                        ["qualified", "offer_sent", "meeting", "eoi", "won"]))
        elif block == "meetings":
            dom.append(("mudon_stage_kind_current", "in",
                        ["meeting", "eoi", "won"]))
        elif block in ("won", "commission", "fin_won",
                       "fin_unbilled", "fin_to_collect"):
            dom.append(WON)
        elif block == "fin_invoiced":
            dom += [WON, ("mudon_invoiced_amount", ">", 0)]
        elif block == "fin_collected":
            dom += [WON, ("mudon_collected_amount", ">", 0)]
        elif block == "agent":
            dom.append(("user_id", "=", self._as_int(key) or False))
        elif block == "source":
            dom.append(("mudon_source_id", "=", self._as_int(key) or False))
        elif block == "nationality":
            dom.append(("mudon_nationality_id", "=", self._as_int(key) or False))
        elif block == "stage":
            dom.append(("mudon_stage_kind_current", "=", key))

        # Client country is phone-derived (no stored field) — resolve to ids.
        pref = key if block == "country" else country_prefix
        if pref:
            leads = self.env["crm.lead"].sudo().search(dom)
            ids = leads.filtered(
                lambda l: self._country_prefix_of(l.phone) == pref).ids
            dom = [("id", "in", ids)]

        team_id = False
        if pipeline in ("turkey", "uae"):
            team = self.env.ref("mudon_crm.mudon_team_%s" % pipeline,
                                raise_if_not_found=False)
            team_id = team.id if team else False
        return {"domain": dom, "name": labels.get(block, "Leads"),
                "team_id": team_id}

    # ── main entry point ────────────────────────────────────────────────
    @api.model
    def get_management_data(self, pipeline="all", period="this_month",
                            basis="pipeline", filters=None):
        Lead = self.env["crm.lead"].sudo()

        d_from, d_to = self._resolve_range(period, filters)
        dt_from = datetime.combine(d_from, time.min)
        dt_to = datetime.combine(d_to, time.max)
        date_field = "create_date" if basis == "pipeline" else "date_closed"

        base_domain = [("type", "=", "opportunity"),
                       ("mudon_pipeline_kind", "!=", False)]
        if pipeline in ("turkey", "uae"):
            base_domain.append(("mudon_pipeline_kind", "=", pipeline))
        base_domain, country_prefix = self._apply_filters(base_domain, filters)

        data = {
            "meta": dict({
                "pipeline": pipeline,
                "period": period,
                "period_label": self._period_label(period, filters),
                "basis": basis,
                "basis_label": "Pipeline Date" if basis == "pipeline" else "Close Date",
                "generated": fields.Datetime.now().strftime("%Y-%m-%d %H:%M"),
            }, **dict(self._currency_meta(pipeline),
                      **self._range_meta(period, filters))),
            "kpis": {}, "tables": {}, "charts": {}, "_errors": [],
        }

        def idx(lead):
            return STAGE_INDEX.get(lead.mudon_stage_kind_current, -1)

        def is_lost(lead):
            return lead.mudon_stage_kind_current == "lost"

        # leads whose basis date falls in the period
        period_domain = base_domain + [
            (date_field, ">=", dt_from), (date_field, "<=", dt_to)]

        # ===================== KPIs + AGENT TABLE =====================
        try:
            leads = Lead.search(period_domain)
            if country_prefix:
                leads = leads.filtered(
                    lambda l: self._country_prefix_of(l.phone) == country_prefix)
            agents = {}   # user -> counters

            def bucket(u):
                return agents.setdefault(u.id if u else 0, {
                    "agent": u.name if u else "Unassigned",
                    "agent_id": u.id if u else False,
                    "assigned": 0, "not_actioned": 0, "lost": 0,
                    "qualified": 0, "offers": 0, "meetings": 0, "won": 0,
                    "revenue": 0.0, "commission": 0.0,
                })

            tot = {"assigned": 0, "not_actioned": 0, "lost": 0, "qualified": 0,
                   "offers": 0, "meetings": 0, "won": 0, "revenue": 0.0,
                   "commission": 0.0}
            for lead in leads:
                b = bucket(lead.user_id)
                b["assigned"] += 1
                tot["assigned"] += 1
                if not lead.mudon_first_contact_logged:
                    b["not_actioned"] += 1
                    tot["not_actioned"] += 1
                if is_lost(lead):
                    b["lost"] += 1
                    tot["lost"] += 1
                i = idx(lead)
                if i >= 1:
                    b["qualified"] += 1
                    tot["qualified"] += 1
                if i >= 2:
                    b["offers"] += 1
                    tot["offers"] += 1
                if i >= 3:
                    b["meetings"] += 1
                    tot["meetings"] += 1
                if i == 5:
                    b["won"] += 1
                    tot["won"] += 1
                    rev = self._to_display_amount(
                        lead, lead.expected_revenue or 0.0, pipeline)
                    com = self._to_display_amount(
                        lead, lead.mudon_commission or 0.0, pipeline)
                    b["revenue"] += rev
                    tot["revenue"] += rev
                    b["commission"] += com
                    tot["commission"] += com

            data["kpis"] = {
                "assigned": tot["assigned"],
                "not_actioned": tot["not_actioned"],
                "qualified": tot["qualified"],
                "meetings": tot["meetings"],
                "won": tot["won"],
                "commission": round(tot["commission"], 2),
            }
            rows = []
            for b in agents.values():
                b["win_rate"] = round(100.0 * b["won"] / b["assigned"], 1) if b["assigned"] else 0.0
                b["revenue"] = round(b["revenue"], 2)
                b["commission"] = round(b["commission"], 2)
                rows.append(b)
            rows.sort(key=lambda r: r["won"], reverse=True)
            tot["win_rate"] = round(100.0 * tot["won"] / tot["assigned"], 1) if tot["assigned"] else 0.0
            tot["agent"] = "TOTAL"
            tot["revenue"] = round(tot["revenue"], 2)
            tot["commission"] = round(tot["commission"], 2)
            data["tables"]["agents"] = rows
            data["tables"]["agents_total"] = tot

            # Client comment 31 - "in turkey i see qualified stage have around
            # 10 leads in kanban but in dashboard it says only 3 qualified".
            # The KPI above is CUMULATIVE (leads that reached qualified or
            # beyond), which is the right funnel metric but is not what the
            # board shows. This block counts leads by the stage they are on
            # RIGHT NOW, so every row matches its kanban column exactly.
            stage_labels = {
                "new_lead": "New Lead", "qualified": "Qualified",
                "offer_sent": "Offer Sent", "meeting": "Meeting",
                "eoi": "EOI / Booking", "won": "WON (SPA Signed)",
                "lost": "Lost",
            }
            current = {k: 0 for k in stage_labels}
            for lead in leads:
                kind = lead.mudon_stage_kind_current
                if kind in current:
                    current[kind] += 1
            data["tables"]["by_stage"] = [
                {"kind": k, "label": stage_labels[k], "count": current[k]}
                for k in ("new_lead", "qualified", "offer_sent", "meeting",
                          "eoi", "won", "lost")
            ]
        except Exception as e:
            data["_errors"].append("agents: %s" % e)
            leads = Lead.browse()

        # ===================== BY CLIENT COUNTRY =====================
        try:
            countries = {}
            for lead in leads:
                label = self._country_label(lead.phone)
                c = countries.setdefault(label, {
                    "country": label,
                    "prefix": self._country_prefix_of(lead.phone),
                    "leads": 0, "won": 0, "revenue": 0.0})
                c["leads"] += 1
                if lead.mudon_stage_kind_current == "won":
                    c["won"] += 1
                    c["revenue"] += lead.expected_revenue or 0.0
            crows = []
            for c in countries.values():
                c["conv"] = round(100.0 * c["won"] / c["leads"], 1) if c["leads"] else 0.0
                c["revenue"] = round(c["revenue"], 2)
                crows.append(c)
            crows.sort(key=lambda r: r["leads"], reverse=True)
            data["tables"]["countries"] = crows
        except Exception as e:
            data["_errors"].append("countries: %s" % e)

        # ===================== BY SOURCE =====================
        try:
            sources = {}
            for lead in leads:
                label = lead.mudon_source_id.name or "Manual / None"
                s = sources.setdefault(label, {
                    "source": label,
                    "source_id": lead.mudon_source_id.id or False,
                    "leads": 0, "won": 0, "commission": 0.0})
                s["leads"] += 1
                if lead.mudon_stage_kind_current == "won":
                    s["won"] += 1
                    s["commission"] += lead.mudon_commission or 0.0
            srows = []
            for s in sources.values():
                s["conv"] = round(100.0 * s["won"] / s["leads"], 1) if s["leads"] else 0.0
                s["commission"] = round(s["commission"], 2)
                srows.append(s)
            srows.sort(key=lambda r: r["leads"], reverse=True)
            data["tables"]["sources"] = srows
        except Exception as e:
            data["_errors"].append("sources: %s" % e)

        # ===================== COMMISSION TREND (monthly, YoY) =====================
        try:
            today = fields.Date.context_today(self)
            y = today.year
            this_year = [0.0] * 12
            last_year = [0.0] * 12
            trend_domain = base_domain + [("mudon_stage_kind_current", "=", "won")]
            for lead in Lead.search(trend_domain):
                if country_prefix and self._country_prefix_of(lead.phone) != country_prefix:
                    continue
                dclose = lead.date_closed or lead.write_date
                if not dclose:
                    continue
                dd = dclose.date() if hasattr(dclose, "date") else dclose
                comm = lead.mudon_commission or 0.0
                if dd.year == y:
                    this_year[dd.month - 1] += comm
                elif dd.year == y - 1:
                    last_year[dd.month - 1] += comm
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            data["charts"]["commission_trend"] = {
                "labels": months,
                "this_year": [round(v, 2) for v in this_year],
                "last_year": [round(v, 2) for v in last_year],
                "year": y,
            }
            ytd = round(sum(this_year), 2)
            best_i = max(range(12), key=lambda i: this_year[i]) if any(this_year) else 0
            active_months = sum(1 for v in this_year if v) or 1
            ly_total = sum(last_year) or 0.0
            data["kpis"]["ytd_commission"] = ytd
            data["kpis"]["best_month"] = {"label": months[best_i], "value": round(this_year[best_i], 2)}
            data["kpis"]["avg_month"] = round(ytd / active_months, 2)
            data["kpis"]["vs_last_year"] = round(100.0 * (ytd - ly_total) / ly_total, 1) if ly_total else None
        except Exception as e:
            data["_errors"].append("trend: %s" % e)

        return data

    # ══════════════════════════════════════════════════════════════════════
    #  FINANCIAL & REVENUE DASHBOARD
    # ══════════════════════════════════════════════════════════════════════
    # Each basis picks the (amount, date) pair the breakdown tables measure:
    #   won      -> commission booked, placed by the deal-won date
    #   invoice  -> amount invoiced,   placed by the invoice date
    #   payment  -> amount collected,  placed by the payment-received date
    _FIN_BASIS = {
        "won":     ("mudon_commission",       "date_closed"),
        "invoice": ("mudon_invoiced_amount",  "mudon_invoiced_date"),
        "payment": ("mudon_collected_amount", "mudon_collected_date"),
    }

    @staticmethod
    def _as_date(value):
        if not value:
            return None
        return value.date() if hasattr(value, "date") else value

    @api.model
    def get_financial_data(self, pipeline="all", period="this_month",
                           basis="won", source="crm", filters=None):
        """Won / Invoiced / Collected / Unbilled + breakdowns + trend.

        source='crm'        -> Invoiced & Collected come from the CRM-native
                               fields the admin fills on each won deal.
        source='accounting' -> Invoiced & Collected headline + trend come from
                               live account.move / account.payment instead
                               (breakdowns stay CRM-tracked; a note explains).
        filters : optional dict {agent_id, source_id, nationality_id,
                  country_prefix, date_from, date_to}. stage_kind is ignored
                  here (this board is won-only).
        """
        Lead = self.env["crm.lead"].sudo()
        if basis not in self._FIN_BASIS:
            basis = "won"
        amount_field, date_field = self._FIN_BASIS[basis]

        d_from, d_to = self._resolve_range(period, filters)
        today = fields.Date.context_today(self)
        y = today.year
        ytd_from, ytd_to = date(y, 1, 1), today
        ly_from, ly_to = date(y - 1, 1, 1), date(y - 1, 12, 31)

        base_domain = [("type", "=", "opportunity"),
                       ("mudon_pipeline_kind", "!=", False),
                       ("mudon_stage_kind_current", "=", "won")]
        if pipeline in ("turkey", "uae"):
            base_domain.append(("mudon_pipeline_kind", "=", pipeline))
        # never let a stray stage_kind override the won-only financial base
        fin_filters = dict(filters or {})
        fin_filters.pop("stage_kind", None)
        base_domain, country_prefix = self._apply_filters(base_domain, fin_filters)

        basis_labels = {"won": "Won Date", "invoice": "Invoice Date",
                        "payment": "Payment Received"}
        data = {
            "meta": dict({
                "pipeline": pipeline,
                "period": period,
                "period_label": self._period_label(period, filters),
                "basis": basis,
                "basis_label": basis_labels[basis],
                "source": source,
                "source_label": ("Accounting (live)" if source == "accounting"
                                 else "CRM (admin-tracked)"),
                "year": y,
                "generated": fields.Datetime.now().strftime("%Y-%m-%d %H:%M"),
            }, **dict(self._currency_meta(pipeline),
                      **self._range_meta(period, filters))),
            "kpis": {}, "tables": {}, "charts": {}, "notes": [], "_errors": [],
        }

        won_leads = Lead.search(base_domain)
        if country_prefix:
            won_leads = won_leads.filtered(
                lambda l: self._country_prefix_of(l.phone) == country_prefix)
        _d = self._as_date

        # ===================== HEADLINE KPIs =====================
        try:
            won_period = inv_crm = col_crm = 0.0
            unbilled = collect_gap = 0.0
            for lead in won_leads:
                comm = lead.mudon_commission or 0.0
                inv = lead.mudon_invoiced_amount or 0.0
                col = lead.mudon_collected_amount or 0.0
                dcl = _d(lead.date_closed)
                idt = _d(lead.mudon_invoiced_date)
                cdt = _d(lead.mudon_collected_date)
                if dcl and d_from <= dcl <= d_to:
                    won_period += comm
                if idt and d_from <= idt <= d_to:
                    inv_crm += inv
                if cdt and d_from <= cdt <= d_to:
                    col_crm += col
                # backlog stocks (all-time), independent of the period filter
                if comm > inv:
                    unbilled += comm - inv
                if inv > col:
                    collect_gap += inv - col

            invoiced_period, collected_period = inv_crm, col_crm
            if source == "accounting":
                acc = self._fin_accounting(d_from, d_to, y)
                if acc is None:
                    data["notes"].append(
                        "Accounting app not installed — showing CRM-tracked "
                        "figures instead.")
                    data["meta"]["source"] = "crm"
                    data["meta"]["source_label"] = "CRM (admin-tracked)"
                    source = "crm"
                else:
                    invoiced_period = acc["invoiced_period"]
                    collected_period = acc["collected_period"]
                    data["notes"].append(
                        "Invoiced & Collected are live Accounting figures "
                        "(company-wide). Won, Unbilled and the breakdown "
                        "tables below stay CRM-tracked.")

            data["kpis"] = {
                "won": round(won_period, 2),
                "invoiced": round(invoiced_period, 2),
                "collected": round(collected_period, 2),
                "unbilled": round(unbilled, 2),
                "to_collect": round(collect_gap, 2),
                "collection_rate": (round(100.0 * collected_period / invoiced_period, 1)
                                    if invoiced_period else None),
            }
        except Exception as e:
            data["_errors"].append("kpis: %s" % e)

        # ===================== BREAKDOWN TABLES (always CRM) =====================
        def _agg(keyfn):
            buckets = {}
            for lead in won_leads:
                amt = lead[amount_field] or 0.0
                if not amt:
                    continue
                dd = _d(lead[date_field])
                if not dd:
                    continue
                key = keyfn(lead) or "—"
                row = buckets.setdefault(key, {
                    "label": key, "this": 0.0, "ytd": 0.0, "last": 0.0})
                if d_from <= dd <= d_to:
                    row["this"] += amt
                if ytd_from <= dd <= ytd_to:
                    row["ytd"] += amt
                if ly_from <= dd <= ly_to:
                    row["last"] += amt
            rows, tot = [], {"label": "TOTAL", "this": 0.0, "ytd": 0.0, "last": 0.0}
            for r in buckets.values():
                for k in ("this", "ytd", "last"):
                    tot[k] += r[k]
                    r[k] = round(r[k], 2)
                rows.append(r)
            rows.sort(key=lambda r: r["ytd"], reverse=True)
            for k in ("this", "ytd", "last"):
                tot[k] = round(tot[k], 2)
            return {"rows": rows, "total": tot}

        try:
            data["tables"]["agents"] = _agg(
                lambda l: l.user_id.name or "Unassigned")
            data["tables"]["countries"] = _agg(
                lambda l: self._country_label(l.phone))
            data["tables"]["nationalities"] = _agg(
                lambda l: l.mudon_nationality_id.name or "Unknown")
            data["tables"]["sources"] = _agg(
                lambda l: l.mudon_source_id.name or "Manual / None")
        except Exception as e:
            data["_errors"].append("tables: %s" % e)

        # ===================== TREND (won vs invoiced vs collected) =====================
        try:
            won_m = [0.0] * 12
            inv_m = [0.0] * 12
            col_m = [0.0] * 12
            for lead in won_leads:
                dcl = _d(lead.date_closed)
                if dcl and dcl.year == y:
                    won_m[dcl.month - 1] += lead.mudon_commission or 0.0
                idt = _d(lead.mudon_invoiced_date)
                if idt and idt.year == y:
                    inv_m[idt.month - 1] += lead.mudon_invoiced_amount or 0.0
                cdt = _d(lead.mudon_collected_date)
                if cdt and cdt.year == y:
                    col_m[cdt.month - 1] += lead.mudon_collected_amount or 0.0
            if source == "accounting":
                acc = self._fin_accounting(d_from, d_to, y)
                if acc is not None:
                    inv_m = acc["inv_m"]
                    col_m = acc["col_m"]
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            data["charts"]["revenue_trend"] = {
                "labels": months,
                "won": [round(v, 2) for v in won_m],
                "invoiced": [round(v, 2) for v in inv_m],
                "collected": [round(v, 2) for v in col_m],
                "year": y,
            }
        except Exception as e:
            data["_errors"].append("trend: %s" % e)

        # ===================== DEAL DETAIL (drill-down) =====================
        try:
            pipe_lbl = {"turkey": "Turkey", "uae": "UAE Dubai"}
            rows = []
            for lead in won_leads:
                bdate = _d(lead[date_field])   # place the deal by the active basis
                if not bdate or not (d_from <= bdate <= d_to):
                    continue
                rows.append({
                    "id": lead.id,
                    "name": lead.name or "—",
                    "agent": lead.user_id.name or "Unassigned",
                    "pipeline": pipe_lbl.get(lead.mudon_pipeline_kind, "—"),
                    "country": self._country_label(lead.phone),
                    "nationality": lead.mudon_nationality_id.name or "Unknown",
                    "source": lead.mudon_source_id.name or "Manual / None",
                    "expected_revenue": round(lead.expected_revenue or 0.0, 2),
                    "commission": round(lead.mudon_commission or 0.0, 2),
                    "invoiced": round(lead.mudon_invoiced_amount or 0.0, 2),
                    "invoiced_date": (_d(lead.mudon_invoiced_date).isoformat()
                                      if lead.mudon_invoiced_date else ""),
                    "collected": round(lead.mudon_collected_amount or 0.0, 2),
                    "collected_date": (_d(lead.mudon_collected_date).isoformat()
                                       if lead.mudon_collected_date else ""),
                    "closed": (_d(lead.date_closed).isoformat()
                               if lead.date_closed else ""),
                })
            rows.sort(key=lambda r: r["closed"] or "", reverse=True)
            data["tables"]["deals"] = {"rows": rows[:500], "count": len(rows)}
        except Exception as e:
            data["_errors"].append("deals: %s" % e)

        return data

    @api.model
    def _fin_accounting(self, d_from, d_to, year):
        """Live Invoiced/Collected from Accounting, or None if not installed."""
        if "account.move" not in self.env or "account.payment" not in self.env:
            return None
        try:
            AM = self.env["account.move"].sudo()
            AP = self.env["account.payment"].sudo()
            inv_moves = AM.search([
                ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
                ("invoice_date", ">=", date(year, 1, 1)),
                ("invoice_date", "<=", date(year, 12, 31))])
            pays = AP.search([
                ("payment_type", "=", "inbound"),
                ("partner_type", "=", "customer"),
                ("state", "not in", ("draft", "canceled", "cancel", "rejected")),
                ("date", ">=", date(year, 1, 1)),
                ("date", "<=", date(year, 12, 31))])
            inv_m = [0.0] * 12
            col_m = [0.0] * 12
            inv_period = col_period = 0.0
            for m in inv_moves:
                idt = self._as_date(m.invoice_date)
                if not idt:
                    continue
                inv_m[idt.month - 1] += m.amount_total
                if d_from <= idt <= d_to:
                    inv_period += m.amount_total
            for p in pays:
                pdt = self._as_date(p.date)
                if not pdt:
                    continue
                col_m[pdt.month - 1] += p.amount
                if d_from <= pdt <= d_to:
                    col_period += p.amount
            return {
                "inv_m": [round(v, 2) for v in inv_m],
                "col_m": [round(v, 2) for v in col_m],
                "invoiced_period": round(inv_period, 2),
                "collected_period": round(col_period, 2),
            }
        except Exception:
            return None

    # ── full deal register (used by the export controller) ──────────────
    @api.model
    def get_deal_register(self, pipeline="all", won_only=False, filters=None,
                          limit=5000, period=None, basis=None):
        """Flat per-deal rows honoring pipeline + all extra filters, for the
        downloadable report's ``Deals`` sheet.

        When ``period`` is given the rows are date-bound to the same window the
        board shows (by the basis date field), so the exported sheet matches
        the on-screen figures. When ``period`` is None it is a complete,
        un-bounded register.
        """
        Lead = self.env["crm.lead"].sudo()
        domain = [("type", "=", "opportunity"),
                  ("mudon_pipeline_kind", "!=", False)]
        if pipeline in ("turkey", "uae"):
            domain.append(("mudon_pipeline_kind", "=", pipeline))
        if won_only:
            domain.append(("mudon_stage_kind_current", "=", "won"))
        ff = dict(filters or {})
        if won_only:
            ff.pop("stage_kind", None)
        domain, country_prefix = self._apply_filters(domain, ff)
        _d = self._as_date

        # optional date-bounding to mirror the board's active window
        d_from = d_to = bound_field = None
        if period is not None:
            d_from, d_to = self._resolve_range(period, filters)
            if won_only:
                bound_field = self._FIN_BASIS.get(
                    basis or "won", self._FIN_BASIS["won"])[1]
            else:
                bound_field = ("create_date" if (basis or "pipeline") == "pipeline"
                               else "date_closed")

        pipe_lbl = {"turkey": "Turkey", "uae": "UAE Dubai"}
        rows = []
        for l in Lead.search(domain, order="create_date desc", limit=limit):
            if country_prefix and self._country_prefix_of(l.phone) != country_prefix:
                continue
            if bound_field:
                dv = _d(l[bound_field])
                if not dv or not (d_from <= dv <= d_to):
                    continue
            rows.append({
                "name": l.name or "",
                "agent": l.user_id.name or "Unassigned",
                "pipeline": pipe_lbl.get(l.mudon_pipeline_kind,
                                         l.mudon_pipeline_kind or ""),
                "stage": (l.mudon_stage_kind_current or "").replace("_", " ").title(),
                "country": self._country_label(l.phone),
                "nationality": l.mudon_nationality_id.name or "",
                "source": l.mudon_source_id.name or "",
                "expected_revenue": round(l.expected_revenue or 0.0, 2),
                "commission": round(l.mudon_commission or 0.0, 2),
                "invoiced": round(l.mudon_invoiced_amount or 0.0, 2),
                "invoiced_date": (_d(l.mudon_invoiced_date).isoformat()
                                  if l.mudon_invoiced_date else ""),
                "collected": round(l.mudon_collected_amount or 0.0, 2),
                "collected_date": (_d(l.mudon_collected_date).isoformat()
                                   if l.mudon_collected_date else ""),
                "created": (_d(l.create_date).isoformat() if l.create_date else ""),
                "closed": (_d(l.date_closed).isoformat() if l.date_closed else ""),
            })
        return rows

    # ── helpers ─────────────────────────────────────────────────────────
    @api.model
    def _country_label(self, phone):
        norm = self.env["crm.lead"]._mudon_phone_normalize(phone or "")
        if norm.startswith("+"):
            digits = norm[1:]
            for prefix, label in COUNTRY_BY_PREFIX:
                if digits.startswith(prefix):
                    return label
        return "Other / Unknown"
