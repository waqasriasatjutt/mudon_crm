# -*- coding: utf-8 -*-
"""HTTP controller that streams the Mudon dashboard as a downloadable report.

Reuses the ``mudon.dashboard`` data provider (single source of truth) so the
file matches the on-screen numbers exactly, honoring every active filter posted
from the OWL board. Builds a multi-sheet XLSX with xlsxwriter (bundled in Odoo
19); on a stripped image where the import fails it degrades to a CSV so the
button never dead-ends.
"""
import csv
import io

from odoo import http
from odoo.http import request, content_disposition

XLSX_MIME = ("application/vnd.openxmlformats-officedocument."
             "spreadsheetml.sheet")
MANAGER_GROUP = "sales_team.group_sale_manager"


class MudonDashboardExport(http.Controller):

    @http.route("/mudon/dashboard/export", type="http", auth="user")
    def export(self, kind="management", pipeline="all", period="this_month",
               basis=None, source="crm", **kw):
        # ---- manager-only scope (a menu group hides UI, not a raw URL) ----
        if not request.env.user.has_group(MANAGER_GROUP):
            raise request.not_found()

        kind = "financial" if kind == "financial" else "management"
        filters = {
            "agent_id": kw.get("agent_id") or None,
            "source_id": kw.get("source_id") or None,
            "nationality_id": kw.get("nationality_id") or None,
            "country_prefix": kw.get("country_prefix") or None,
            "stage_kind": kw.get("stage_kind") or None,
            "date_from": kw.get("date_from") or None,
            "date_to": kw.get("date_to") or None,
        }

        Dash = request.env["mudon.dashboard"].sudo()
        if kind == "financial":
            basis = basis or "won"
            data = Dash.get_financial_data(pipeline, period, basis, source,
                                           filters)
            deals = Dash.get_deal_register(pipeline, True, filters)
            stub = "mudon_financial_report"
        else:
            basis = basis or "pipeline"
            data = Dash.get_management_data(pipeline, period, basis, filters)
            deals = Dash.get_deal_register(pipeline, False, filters)
            stub = "mudon_management_report"

        meta = data.get("meta", {})
        tag = "%s_%s" % (meta.get("pipeline", pipeline),
                         meta.get("period", period))

        try:
            import xlsxwriter  # noqa: PLC0415 — lazy, matches core's pattern
        except ImportError:
            body = self._build_csv(kind, data, deals)
            return self._respond(body, "%s_%s.csv" % (stub, tag),
                                  "text/csv;charset=utf-8")

        body = self._build_xlsx(xlsxwriter, kind, data, deals)
        return self._respond(body, "%s_%s.xlsx" % (stub, tag), XLSX_MIME)

    # -- response helper ------------------------------------------------------
    def _respond(self, body, filename, mimetype):
        return request.make_response(body, headers=[
            ("Content-Type", mimetype),
            ("Content-Disposition", content_disposition(filename)),
        ])

    # ========================================================================
    #  XLSX
    # ========================================================================
    def _build_xlsx(self, xlsxwriter, kind, data, deals):
        out = io.BytesIO()
        wb = xlsxwriter.Workbook(out, {"in_memory": True})
        meta = data.get("meta", {})
        cur = meta.get("currency", "")

        f_title = wb.add_format({"bold": True, "font_size": 14})
        f_meta = wb.add_format({"italic": True, "font_color": "#555555"})
        f_head = wb.add_format({"bold": True, "bg_color": "#1b2b44",
                                "font_color": "#ffffff", "border": 1})
        f_txt = wb.add_format({"border": 1})
        f_num = wb.add_format({"num_format": "#,##0", "border": 1})
        f_money = wb.add_format(
            {"num_format": '#,##0.00 "%s"' % cur if cur else "#,##0.00",
             "border": 1})
        f_pct = wb.add_format({"num_format": "0.0", "border": 1})
        f_total = wb.add_format({"bold": True, "top": 2, "border": 1,
                                 "bg_color": "#faf9f5"})
        fmts = {"txt": f_txt, "num": f_num, "money": f_money, "pct": f_pct}

        def write_head(ws, title):
            ws.write(0, 0, title, f_title)
            line = "Pipeline: %s   |   Period: %s   |   Basis: %s" % (
                meta.get("pipeline", ""), meta.get("period_label", ""),
                meta.get("basis_label", ""))
            if kind == "financial":
                line += "   |   Source: %s" % meta.get("source_label", "")
            ws.write(1, 0, line, f_meta)
            ws.write(2, 0, "Generated: %s" % meta.get("generated", ""), f_meta)

        def sheet_table(name, columns, rows, total=None):
            ws = wb.add_worksheet(name[:31])
            write_head(ws, name)
            r0 = 4
            for c, (hdr, _k, _f) in enumerate(columns):
                ws.write(r0, c, hdr, f_head)
            r = r0 + 1
            for row in rows or []:
                for c, (_h, key, fk) in enumerate(columns):
                    ws.write(r, c, row.get(key, "" if fk == "txt" else 0),
                             fmts[fk])
                r += 1
            if total:
                for c, (_h, key, fk) in enumerate(columns):
                    ws.write(r, c, total.get(key, "" if fk == "txt" else 0),
                             f_total)
            ws.set_column(0, 0, 28)
            if len(columns) > 1:
                ws.set_column(1, len(columns) - 1, 16)

        # -------- Summary (KPIs) --------
        ws = wb.add_worksheet("Summary")
        write_head(ws, "Summary — KPIs")
        ws.write(4, 0, "KPI", f_head)
        ws.write(4, 1, "Value", f_head)
        r = 5
        for label, val in self._kpi_pairs(kind, data):
            ws.write(r, 0, label, f_txt)
            ws.write(r, 1, "" if val is None else val, f_txt)
            r += 1
        ws.set_column(0, 0, 30)
        ws.set_column(1, 1, 22)

        tables = data.get("tables", {})
        if kind == "management":
            sheet_table("Agents",
                        [("Agent", "agent", "txt"),
                         ("Assigned", "assigned", "num"),
                         ("Not actioned", "not_actioned", "num"),
                         ("Lost", "lost", "num"),
                         ("Qualified", "qualified", "num"),
                         ("Offers", "offers", "num"),
                         ("Meetings", "meetings", "num"),
                         ("Won", "won", "num"),
                         ("Win rate %", "win_rate", "pct"),
                         ("Revenue", "revenue", "money"),
                         ("Commission", "commission", "money")],
                        tables.get("agents", []),
                        tables.get("agents_total"))
            sheet_table("Countries",
                        [("Country", "country", "txt"),
                         ("Leads", "leads", "num"), ("Won", "won", "num"),
                         ("Conv %", "conv", "pct"),
                         ("Revenue", "revenue", "money")],
                        tables.get("countries", []))
            sheet_table("Sources",
                        [("Source", "source", "txt"),
                         ("Leads", "leads", "num"), ("Won", "won", "num"),
                         ("Conv %", "conv", "pct"),
                         ("Commission", "commission", "money")],
                        tables.get("sources", []))
        else:
            year = meta.get("year", "")
            last_hdr = str(year - 1) if isinstance(year, int) else "Last year"
            fin_cols = [("Name", "label", "txt"),
                        (meta.get("period_label", "This period"), "this", "money"),
                        ("YTD %s" % year, "ytd", "money"),
                        (last_hdr, "last", "money")]
            for name, tkey in (("Rev by Agent", "agents"),
                               ("Rev by Country", "countries"),
                               ("Rev by Nationality", "nationalities"),
                               ("Rev by Source", "sources")):
                blk = tables.get(tkey, {}) or {}
                sheet_table(name, fin_cols, blk.get("rows", []),
                            blk.get("total"))

        # -------- Deals register --------
        self._sheet_deals(wb, f_head, f_txt, f_money, deals)
        wb.close()
        return out.getvalue()

    def _kpi_pairs(self, kind, data):
        k = data.get("kpis", {})
        if kind == "management":
            pairs = [("Assigned leads", k.get("assigned", 0)),
                     ("Not actioned", k.get("not_actioned", 0)),
                     ("Qualified", k.get("qualified", 0)),
                     ("Meetings", k.get("meetings", 0)),
                     ("Won", k.get("won", 0)),
                     ("Commission", k.get("commission", 0)),
                     ("YTD commission", k.get("ytd_commission", 0)),
                     ("Avg / month", k.get("avg_month", 0))]
            bm = k.get("best_month")
            if bm:
                pairs.append(("Best month",
                              "%s (%s)" % (bm.get("label"), bm.get("value"))))
            return pairs
        return [("Won (commission)", k.get("won", 0)),
                ("Invoiced", k.get("invoiced", 0)),
                ("Collected", k.get("collected", 0)),
                ("Unbilled (backlog)", k.get("unbilled", 0)),
                ("Yet to collect", k.get("to_collect", 0)),
                ("Collection rate %", k.get("collection_rate"))]

    _DEAL_COLS = [
        ("Deal", "name"), ("Agent", "agent"), ("Pipeline", "pipeline"),
        ("Stage", "stage"), ("Country", "country"),
        ("Nationality", "nationality"), ("Source", "source"),
        ("Expected revenue", "expected_revenue"), ("Commission", "commission"),
        ("Invoiced", "invoiced"), ("Invoice date", "invoiced_date"),
        ("Collected", "collected"), ("Payment date", "collected_date"),
        ("Created", "created"), ("Closed", "closed"),
    ]
    _DEAL_MONEY = {"expected_revenue", "commission", "invoiced", "collected"}

    def _sheet_deals(self, wb, f_head, f_txt, f_money, deals):
        ws = wb.add_worksheet("Deals")
        for c, (hdr, _k) in enumerate(self._DEAL_COLS):
            ws.write(0, c, hdr, f_head)
        r = 1
        for row in deals or []:
            for c, (_h, key) in enumerate(self._DEAL_COLS):
                fmt = f_money if key in self._DEAL_MONEY else f_txt
                ws.write(r, c, row.get(key, ""), fmt)
            r += 1
        ws.set_column(0, 0, 30)
        ws.set_column(1, len(self._DEAL_COLS) - 1, 16)

    # ========================================================================
    #  CSV fallback (KPIs + Deals in one flat file)
    # ========================================================================
    def _build_csv(self, kind, data, deals):
        buf = io.StringIO()
        w = csv.writer(buf)
        meta = data.get("meta", {})
        w.writerow(["Mudon %s report" % kind])
        w.writerow(["Pipeline", meta.get("pipeline", ""),
                    "Period", meta.get("period_label", ""),
                    "Basis", meta.get("basis_label", "")])
        w.writerow([])
        w.writerow(["KPI", "Value"])
        for label, val in self._kpi_pairs(kind, data):
            w.writerow([label, "" if val is None else val])
        w.writerow([])
        w.writerow([h for h, _k in self._DEAL_COLS])
        for row in deals or []:
            w.writerow([row.get(k, "") for _h, k in self._DEAL_COLS])
        return buf.getvalue().encode("utf-8-sig")  # BOM → Excel opens UTF-8
