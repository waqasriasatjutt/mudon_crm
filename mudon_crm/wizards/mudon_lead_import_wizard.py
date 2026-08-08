"""Bulk import of leads from Excel or CSV into the New Lead stage.

Client requirement: "Import Lead from Excel into NEW LEAD. We can design
excel format to follow with all the fields."

Odoo's generic importer can technically load crm.lead, but it asks the user
to map columns to technical field names, expects exact master names or
external ids, and reports errors one at a time. This wizard is built around
the client's own column list instead:

  * a downloadable template carrying those exact headers, plus a second sheet
    listing every valid value for each dropdown, so the office cannot guess
    a name wrong;
  * name matching that is case and whitespace insensitive, scoped to the
    pipeline being imported (a Turkey import will not accept Dubai);
  * a validate-only pass so a file can be checked before anything is created;
  * a per-row report naming the row number and the exact problem, with the
    good rows still imported.

Three behaviours are deliberately suppressed during an import, via the
`mudon_import_mode` context flag:

  * the WhatsApp greeting and the agent notification, because importing 500
    leads must not fire 1000 messages;
  * the auto-advance to Qualified, because the requirement is explicitly
    that imported leads land on NEW LEAD even when every field is filled.

Automatic salesperson routing IS still applied when the Salesperson column
is left blank, which is the normal case for a bulk list.
"""
import base64
import io
import logging
import re
from datetime import date, datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# The client's column list, in their words and their order. The key is the
# lowercased header we match against, so the office can retype the header
# with different capitalisation and it still lines up.
COLUMNS = [
    ("contact", "Contact"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("salesperson", "Salesperson"),
    ("service", "Service"),
    ("city", "City"),
    ("priority", "Priority"),
    ("expected revenue", "Expected Revenue"),
    ("source", "Source"),
    ("nationality", "Nationality"),
    ("living in", "Living In"),
    ("in turkey now", "In Turkey Now"),
    ("branch", "Branch"),
    ("no. of beds", "No. of Beds"),
    ("expected visit date", "Expected Visit Date"),
    ("requirements", "Requirements"),
    ("purpose of the property", "Purpose of the Property"),
    ("property type", "Property Type"),
]

TRUE_WORDS = {"yes", "y", "true", "1", "t", "نعم"}
FALSE_WORDS = {"no", "n", "false", "0", "f", "", "لا"}


class MudonLeadImportWizard(models.TransientModel):
    _name = "mudon.lead.import.wizard"
    _description = "Mudon — Import leads from Excel / CSV"

    pipeline_kind = fields.Selection(
        [("turkey", "Turkey Pipeline"), ("uae", "UAE Dubai Pipeline")],
        string="Import into", required=True, default="turkey",
        help="Which pipeline the leads belong to. This decides the sales "
             "team, the New Lead stage, the currency and which Service and "
             "City values are accepted.",
    )
    file_data = fields.Binary(string="File", attachment=False)
    file_name = fields.Char(string="File name")
    skip_duplicates = fields.Boolean(
        string="Skip rows whose phone already exists", default=True,
        help="Compares digits only, so +971 50 123 4567 and 0501234567 count "
             "as the same number within this pipeline.",
    )
    state = fields.Selection(
        [("choose", "choose"), ("done", "done")],
        default="choose",
    )
    result_html = fields.Html(string="Result", readonly=True)

    # ─── Template download ──────────────────────────────────────────────
    def action_download_template(self):
        """Build the blank sheet the office fills in, plus a reference sheet
        listing every valid dropdown value for the chosen pipeline."""
        self.ensure_one()
        import xlsxwriter

        buf = io.BytesIO()
        book = xlsxwriter.Workbook(buf, {"in_memory": True})
        head = book.add_format({
            "bold": True, "bg_color": "#1b2b44", "font_color": "#ffffff",
            "border": 1, "text_wrap": True, "valign": "vcenter",
        })
        note = book.add_format({"italic": True, "font_color": "#8a6d3b"})

        sheet = book.add_worksheet("Leads")
        for col, (_key, label) in enumerate(COLUMNS):
            sheet.write(0, col, label, head)
            sheet.set_column(col, col, max(14, min(28, len(label) + 6)))
        sheet.freeze_panes(1, 0)

        # One filled example row so the expected formatting is obvious.
        example = {
            "Contact": "Ahmed Al Harbi",
            "Email": "ahmed@example.com",
            "Phone": "+971 50 123 4567",
            "Priority": "Urgent",
            "Expected Revenue": 1500000,
            "In Turkey Now": "No",
            "No. of Beds": 3,
            "Expected Visit Date": "2026-09-15",
            "Requirements": "Sea view, high floor",
        }
        for col, (_key, label) in enumerate(COLUMNS):
            if label in example:
                sheet.write(1, col, example[label])
        sheet.write(3, 0, "Delete the example row before importing.", note)
        sheet.write(
            4, 0,
            "Branch is filled automatically from City, anything typed there "
            "is ignored.", note)
        sheet.write(
            5, 0,
            "Leave Salesperson blank to let the system assign the lead by "
            "city branch, then phone country code, then round robin.", note)

        # Reference sheet: the exact values that will be accepted.
        ref = book.add_worksheet("Valid values")
        ref.set_column(0, 0, 26)
        ref.set_column(1, 1, 60)
        ref.write(0, 0, "Field", head)
        ref.write(0, 1, "Accepted values (type one of these)", head)
        row = 1
        for label, values in self._reference_values().items():
            ref.write(row, 0, label)
            ref.write(row, 1, ", ".join(values) if values else "(none set up yet)")
            row += 1

        book.close()
        data = base64.b64encode(buf.getvalue())
        name = "mudon_lead_import_template_%s.xlsx" % self.pipeline_kind
        attach = self.env["ir.attachment"].create({
            "name": name, "datas": data, "type": "binary",
            "mimetype": "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet",
        })
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attach.id,
            "target": "self",
        }

    def _reference_values(self):
        """Valid dropdown values, scoped to the pipeline being imported."""
        self.ensure_one()
        pk = self.pipeline_kind
        scope = [("pipeline_kind", "in", [pk, "any"])]

        def names(model, domain=None):
            return self.env[model].sudo().search(domain or []).mapped("name")

        return {
            "Priority": ["Urgent", "Normal"],
            "In Turkey Now": ["Yes", "No"],
            "Service": names("mudon.service", scope),
            "City": names("mudon.city", scope),
            "Source": names("mudon.source"),
            "Purpose of the Property": names("mudon.purpose", scope),
            "Property Type": names("mudon.property.type"),
            "Salesperson": self._team().member_ids.mapped("name"),
            "Nationality / Living In": ["Any country name, e.g. "
                                        "United Arab Emirates, Turkey, "
                                        "Saudi Arabia"],
            "Expected Visit Date": ["A date, e.g. 2026-09-15 or 15/09/2026"],
            "Purpose / Property Type (multiple)":
                ["Separate several values with a comma"],
        }

    # ─── Helpers ────────────────────────────────────────────────────────
    def _team(self):
        self.ensure_one()
        xmlid = ("mudon_crm.mudon_team_turkey" if self.pipeline_kind == "turkey"
                 else "mudon_crm.mudon_team_uae")
        team = self.env.ref(xmlid, raise_if_not_found=False)
        if not team:
            raise UserError(_("The %s sales team is missing.")
                            % self.pipeline_kind)
        return team

    def _new_lead_stage(self):
        self.ensure_one()
        stage = self.env["crm.stage"].sudo().search([
            ("team_ids", "=", self._team().id),
            ("mudon_stage_kind", "=", "new_lead"),
        ], limit=1)
        if not stage:
            raise UserError(_("This pipeline has no New Lead stage."))
        return stage

    @staticmethod
    def _clean(value):
        if value is None:
            return ""
        if isinstance(value, float) and value == int(value):
            value = int(value)
        return str(value).strip()

    def _read_rows(self):
        """Return (headers, rows) from the uploaded xlsx or csv."""
        self.ensure_one()
        if not self.file_data:
            raise UserError(_("Choose a file first."))
        raw = base64.b64decode(self.file_data)
        name = (self.file_name or "").lower()

        if name.endswith(".csv") or name.endswith(".txt"):
            import csv
            text = raw.decode("utf-8-sig", "replace")
            reader = csv.reader(io.StringIO(text))
            table = [list(r) for r in reader]
        else:
            try:
                import openpyxl
            except ImportError:
                raise UserError(_("Excel support is unavailable on this "
                                  "server. Save the file as CSV and retry."))
            try:
                book = openpyxl.load_workbook(
                    io.BytesIO(raw), data_only=True, read_only=True)
            except Exception as exc:
                raise UserError(
                    _("That file could not be opened as Excel (%s). Use the "
                      "downloaded template, or save it as .xlsx or .csv.")
                    % exc)
            sheet = book.worksheets[0]
            table = [[c for c in row] for row in sheet.iter_rows(values_only=True)]

        table = [r for r in table if any(self._clean(c) for c in r)]
        if not table:
            raise UserError(_("The file has no rows."))

        headers = [self._clean(h).lower() for h in table[0]]
        known = {key for key, _label in COLUMNS}
        if not (set(headers) & known):
            raise UserError(_(
                "The first row does not look like the template headers. "
                "Download the template and keep its header row."))
        return headers, table[1:]

    # ─── Value resolution ───────────────────────────────────────────────
    def _match_master(self, model, value, scoped=False):
        """Case and whitespace insensitive name match, pipeline scoped."""
        text = self._clean(value)
        if not text:
            return None, None
        domain = [("pipeline_kind", "in", [self.pipeline_kind, "any"])] if scoped else []
        records = self.env[model].sudo().search(domain)
        for rec in records:
            if (rec.name or "").strip().lower() == text.lower():
                return rec, None
        options = ", ".join(records.mapped("name")[:12]) or "(none configured)"
        return None, _("\"%s\" is not a valid value. Accepted: %s") % (text, options)

    def _match_many(self, model, value, scoped=False):
        text = self._clean(value)
        if not text:
            return [], None
        ids, errors = [], []
        for part in re.split(r"[,;]", text):
            if not part.strip():
                continue
            rec, err = self._match_master(model, part, scoped=scoped)
            if err:
                errors.append(err)
            else:
                ids.append(rec.id)
        return ids, "; ".join(errors) if errors else None

    def _match_country(self, value):
        text = self._clean(value)
        if not text:
            return None, None
        Country = self.env["res.country"].sudo()
        rec = Country.search(["|", ("name", "=ilike", text),
                              ("code", "=ilike", text)], limit=1)
        if rec:
            return rec, None
        return None, _("\"%s\" is not a country name we recognise.") % text

    def _match_user(self, value):
        text = self._clean(value)
        if not text:
            return None, None
        Users = self.env["res.users"].sudo()
        rec = Users.search(["|", ("login", "=ilike", text),
                            ("name", "=ilike", text)], limit=1)
        if rec:
            return rec, None
        return None, _("\"%s\" is not a user on this system.") % text

    @staticmethod
    def _to_bool(value, cleaner):
        text = cleaner(value).lower()
        if text in TRUE_WORDS:
            return True, None
        if text in FALSE_WORDS:
            return False, None
        return None, _("\"%s\" should be Yes or No.") % text

    def _to_date(self, value):
        if not value:
            return None, None
        if isinstance(value, datetime):
            return value.date(), None
        if isinstance(value, date):
            return value, None
        text = self._clean(value)
        if not text:
            return None, None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y",
                    "%d.%m.%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt).date(), None
            except ValueError:
                continue
        return None, _("\"%s\" is not a date we can read. Use 2026-09-15 "
                       "or 15/09/2026.") % text

    def _to_number(self, value, integer=False):
        text = self._clean(value)
        if not text:
            return 0, None
        cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
        if not cleaned:
            return 0, _("\"%s\" is not a number.") % text
        try:
            return (int(float(cleaned)) if integer else float(cleaned)), None
        except ValueError:
            return 0, _("\"%s\" is not a number.") % text

    # ─── Import ─────────────────────────────────────────────────────────
    def action_validate(self):
        return self._run(dry_run=True)

    def action_import(self):
        return self._run(dry_run=False)

    def _run(self, dry_run=False):
        self.ensure_one()
        headers, rows = self._read_rows()
        idx = {key: headers.index(key) for key, _l in COLUMNS if key in headers}

        def cell(row, key):
            i = idx.get(key)
            return row[i] if i is not None and i < len(row) else None

        team = self._team()
        stage = self._new_lead_stage()
        Lead = self.env["crm.lead"]

        existing_phones = set()
        if self.skip_duplicates:
            for lead in Lead.sudo().search(
                    [("mudon_pipeline_kind", "=", self.pipeline_kind)]):
                digits = re.sub(r"\D", "", lead.phone or "")
                if digits:
                    existing_phones.add(digits[-9:])

        to_create, problems, skipped = [], [], 0

        for n, row in enumerate(rows, start=2):   # row 1 is the header
            errors = []
            contact = self._clean(cell(row, "contact"))
            if not contact:
                problems.append((n, _("Contact is empty.")))
                continue

            vals = {
                "name": contact,
                "contact_name": contact,
                "type": "opportunity",
                "team_id": team.id,
                "stage_id": stage.id,
            }

            email = self._clean(cell(row, "email"))
            if email:
                vals["email_from"] = email

            phone = self._clean(cell(row, "phone"))
            if phone:
                vals["phone"] = phone
                digits = re.sub(r"\D", "", phone)
                if self.skip_duplicates and digits and digits[-9:] in existing_phones:
                    skipped += 1
                    continue
                if digits:
                    existing_phones.add(digits[-9:])

            user, err = self._match_user(cell(row, "salesperson"))
            if err:
                errors.append(err)
            elif user:
                vals["user_id"] = user.id

            service, err = self._match_master(
                "mudon.service", cell(row, "service"), scoped=True)
            if err:
                errors.append(err)
            elif service:
                vals["mudon_service_id"] = service.id

            city, err = self._match_master(
                "mudon.city", cell(row, "city"), scoped=True)
            if err:
                errors.append(err)
            elif city:
                vals["mudon_city_id"] = city.id

            priority = self._clean(cell(row, "priority")).lower()
            if priority:
                if priority in ("urgent", "normal"):
                    vals["mudon_priority"] = priority
                else:
                    errors.append(_("Priority \"%s\" should be Urgent or "
                                    "Normal.") % priority)

            revenue, err = self._to_number(cell(row, "expected revenue"))
            if err:
                errors.append(_("Expected Revenue: %s") % err)
            elif revenue:
                vals["expected_revenue"] = revenue

            source, err = self._match_master("mudon.source", cell(row, "source"))
            if err:
                errors.append(err)
            elif source:
                vals["mudon_source_id"] = source.id

            country, err = self._match_country(cell(row, "nationality"))
            if err:
                errors.append(_("Nationality: %s") % err)
            elif country:
                vals["mudon_nationality_id"] = country.id

            country, err = self._match_country(cell(row, "living in"))
            if err:
                errors.append(_("Living In: %s") % err)
            elif country:
                vals["mudon_living_in_id"] = country.id

            raw_flag = cell(row, "in turkey now")
            if self._clean(raw_flag):
                flag, err = self._to_bool(raw_flag, self._clean)
                if err:
                    errors.append(_("In Turkey Now: %s") % err)
                else:
                    vals["mudon_in_country"] = flag

            beds, err = self._to_number(cell(row, "no. of beds"), integer=True)
            if err:
                errors.append(_("No. of Beds: %s") % err)
            elif beds:
                vals["mudon_beds"] = beds

            visit, err = self._to_date(cell(row, "expected visit date"))
            if err:
                errors.append(_("Expected Visit Date: %s") % err)
            elif visit:
                vals["mudon_visit_date"] = visit

            requirements = self._clean(cell(row, "requirements"))
            if requirements:
                vals["mudon_other_specs"] = requirements

            ids, err = self._match_many(
                "mudon.purpose", cell(row, "purpose of the property"),
                scoped=True)
            if err:
                errors.append(_("Purpose: %s") % err)
            elif ids:
                vals["mudon_purpose_ids"] = [(6, 0, ids)]

            ids, err = self._match_many(
                "mudon.property.type", cell(row, "property type"))
            if err:
                errors.append(_("Property Type: %s") % err)
            elif ids:
                vals["mudon_property_type_ids"] = [(6, 0, ids)]

            if errors:
                problems.append((n, " ".join(errors)))
            else:
                to_create.append(vals)

        created = 0
        if to_create and not dry_run:
            # Import mode: route the lead, but do not greet the client, do not
            # notify the agent, and do not auto-advance off New Lead.
            ctx = dict(self.env.context, mudon_import_mode=True,
                       mudon_import_pipeline=self.pipeline_kind)
            for vals in to_create:
                try:
                    Lead.with_context(**ctx).create(vals)
                    created += 1
                except Exception as exc:
                    problems.append((0, _("%s could not be created: %s")
                                     % (vals.get("name"), exc)))

        return self._report(dry_run, len(rows), created, len(to_create),
                            skipped, problems)

    def _report(self, dry_run, total, created, ready, skipped, problems):
        from markupsafe import Markup, escape
        lines = [
            Markup("<h4>%s</h4>") % (
                _("Validation result") if dry_run else _("Import complete")),
            Markup("<ul>"),
            Markup("<li>%s</li>") % (_("Rows read: %s") % total),
        ]
        if dry_run:
            lines.append(Markup("<li>%s</li>")
                         % (_("Rows ready to import: %s") % ready))
        else:
            lines.append(Markup("<li><b>%s</b></li>")
                         % (_("Leads created: %s") % created))
        if skipped:
            lines.append(Markup("<li>%s</li>")
                         % (_("Skipped as duplicate phone: %s") % skipped))
        lines.append(Markup("<li>%s</li>")
                     % (_("Rows with problems: %s") % len(problems)))
        lines.append(Markup("</ul>"))

        if problems:
            lines.append(Markup("<p>%s</p>") % _(
                "These rows were not imported. Fix them in the file and "
                "import again, the rest are already in."))
            lines.append(Markup(
                "<table class='table table-sm'><tr><th>%s</th><th>%s</th></tr>")
                % (escape(_("Row")), escape(_("Problem"))))
            for rownum, msg in problems[:200]:
                lines.append(Markup("<tr><td>%s</td><td>%s</td></tr>")
                             % (rownum or "-", escape(msg)))
            lines.append(Markup("</table>"))
            if len(problems) > 200:
                lines.append(Markup("<p>%s</p>") % (
                    _("...and %s more.") % (len(problems) - 200)))

        self.result_html = Markup("").join(lines)
        self.state = "done"
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": self.env.context,
        }

    def action_open_imported(self):
        """Show the New Lead column of the pipeline just imported into."""
        self.ensure_one()
        xmlid = ("mudon_crm.mudon_pipeline_turkey_action"
                 if self.pipeline_kind == "turkey"
                 else "mudon_crm.mudon_pipeline_uae_action")
        return self.env["ir.actions.act_window"]._for_xml_id(xmlid)
