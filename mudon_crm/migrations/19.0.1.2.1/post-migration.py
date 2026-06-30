"""Post-migration for mudon_crm 19.0.1.2.1.

v1.2.1 adds a `pipeline_kind` column to mudon.service and mudon.purpose
so the lead form can restrict MService / Purpose options per pipeline
(UAE must not be offered the Turkey-only "Citizenship"; Turkey must not
be offered the UAE-only "Golden Visa").

The masters seed file is noupdate="1", so on an EXISTING install the new
column only gets the field default ('any') — the per-record tags from the
XML are not re-applied. This backfills the two pipeline-specific rows by
their stable `code`. Investment + the shared purposes correctly stay
'any'. Idempotent.
"""
import logging

_logger = logging.getLogger(__name__)


def _tag(cr, table, code, kind):
    cr.execute(
        "UPDATE {t} SET pipeline_kind = %s WHERE code = %s".format(t=table),
        (kind, code),
    )
    _logger.info("mudon_crm 1.2.1: %s code=%s -> pipeline_kind=%s (%s row)",
                 table, code, kind, cr.rowcount)


def migrate(cr, version):
    _tag(cr, "mudon_service", "citizenship", "turkey")
    _tag(cr, "mudon_service", "goldenvisa", "uae")
    _tag(cr, "mudon_purpose", "citizenship", "turkey")
    _tag(cr, "mudon_purpose", "goldenvisa", "uae")
