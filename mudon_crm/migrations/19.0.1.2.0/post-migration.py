"""Post-migration for mudon_crm 19.0.1.2.0.

The Stage-1 Selection fields became admin-managed master tables:

  mudon_service        -> mudon_service_id        (mudon.service)
  mudon_city           -> mudon_city_ids          (mudon.city,          m2m)
  mudon_status         -> mudon_status_id         (mudon.lead.status)
  mudon_purpose        -> mudon_purpose_ids       (mudon.purpose,       m2m)
  mudon_property_type  -> mudon_property_type_ids (mudon.property.type, m2m)
  mudon_source         -> mudon_source_id         (mudon.source)
  mudon_lost_reason    -> mudon_lost_reason_id    (mudon.lost.reason)

Each seeded master `code` matches the old Selection key 1-for-1, so
existing leads are remapped by joining old-column == master.code. The
old varchar columns linger after the field rename, so we read them
directly. Runs after the seed data loads (masters exist). Guarded +
idempotent — a repeat upgrade is a no-op.
"""
import logging

_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def _migrate_many2one(cr, old_col, fk_col, master_table):
    if not _column_exists(cr, "crm_lead", old_col):
        return
    cr.execute(
        """
        UPDATE crm_lead l
           SET {fk} = m.id
          FROM {master} m
         WHERE m.code = l.{old}
           AND l.{old} IS NOT NULL
           AND l.{fk} IS NULL
        """.format(fk=fk_col, master=master_table, old=old_col)
    )
    _logger.info("mudon_crm 1.2.0: %s -> %s remapped %s lead(s)",
                 old_col, fk_col, cr.rowcount)


def _migrate_many2many(cr, old_col, rel_table, lead_fk, master_fk, master_table):
    if not _column_exists(cr, "crm_lead", old_col):
        return
    cr.execute(
        """
        INSERT INTO {rel} ({lead_fk}, {master_fk})
        SELECT l.id, m.id
          FROM crm_lead l
          JOIN {master} m ON m.code = l.{old}
         WHERE l.{old} IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM {rel} r
                WHERE r.{lead_fk} = l.id AND r.{master_fk} = m.id
           )
        """.format(rel=rel_table, lead_fk=lead_fk, master_fk=master_fk,
                   master=master_table, old=old_col)
    )
    _logger.info("mudon_crm 1.2.0: %s -> %s linked %s row(s)",
                 old_col, rel_table, cr.rowcount)


def migrate(cr, version):
    _migrate_many2one(cr, "mudon_service", "mudon_service_id", "mudon_service")
    _migrate_many2one(cr, "mudon_status", "mudon_status_id", "mudon_lead_status")
    _migrate_many2one(cr, "mudon_source", "mudon_source_id", "mudon_source")
    _migrate_many2one(cr, "mudon_lost_reason", "mudon_lost_reason_id",
                      "mudon_lost_reason")

    _migrate_many2many(cr, "mudon_city", "crm_lead_mudon_city_rel",
                       "lead_id", "city_id", "mudon_city")
    _migrate_many2many(cr, "mudon_purpose", "crm_lead_mudon_purpose_rel",
                       "lead_id", "purpose_id", "mudon_purpose")
    _migrate_many2many(cr, "mudon_property_type",
                       "crm_lead_mudon_property_type_rel",
                       "lead_id", "property_type_id", "mudon_property_type")
