from . import branch
from . import country_agent_mapping
from . import developer
from . import admin_task
from . import after_sales_task
from . import crm_stage
# v19.0.1.2.0 — master tables (previously Selection enums)
from . import mudon_service
from . import mudon_city
from . import mudon_lead_status
from . import mudon_purpose
from . import mudon_property_type
from . import mudon_source
from . import mudon_lost_reason
from . import mudon_project
from . import mudon_wa_message
from . import res_config_settings
from . import res_users
# crm.lead extension last so the relational fields can ref the masters above
from . import crm_lead
