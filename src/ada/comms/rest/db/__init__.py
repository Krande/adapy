"""Postgres layer for the multi-tenant REST viewer.

Optional. When ``DATABASE_URL`` is empty the pool stays ``None`` and
the API serves in shared-only mode: every authenticated user lands in
the same shared bucket, no projects, no admin panel, no audit log.
This keeps the helm chart genuinely "Postgres-optional" — small
deployments don't need to stand up a database to use the viewer.

Migrations are bundled SQL files under ``migrations/`` and applied at
boot inside an advisory lock so a multi-replica rollout doesn't race.
A row in ``schema_version`` records each applied file by stem name.

Repository helpers expose the small set of queries the rest of the
package needs (project list, membership check, audit log insert).
asyncpg's pool is held on FastAPI's ``app.state.db_pool`` and pulled
via the :func:`get_pool` accessor.
"""

from __future__ import annotations

from ._common import _loads_jsonb as _loads_jsonb  # noqa: F401
from .audit_log import (  # noqa: F401
    _AUDIT_RUN_COUNTER_FOR_STATUS as _AUDIT_RUN_COUNTER_FOR_STATUS,
)
from .audit_log import _bump_audit_run_counter as _bump_audit_run_counter  # noqa: F401
from .audit_log import (
    active_audit_summary,
    admin_cancel_audit_by_job,
    append_metrics_sample_by_job,
    audit_is_cancelled,
    cancel_audit_by_job,
    get_audit_owner_by_job,
    insert_audit,
    mark_audit_running,
    reset_audit_cell_for_rerun,
    update_audit_by_job,
)
from .audit_queries import _RELATIVE_BOUND as _RELATIVE_BOUND  # noqa: F401
from .audit_queries import _RELATIVE_UNIT as _RELATIVE_UNIT  # noqa: F401
from .audit_queries import _audit_predicates as _audit_predicates  # noqa: F401
from .audit_queries import (
    clear_audit_metrics,
    get_audit_by_id,
    get_audit_by_job,
    list_audit,
    parse_audit_time_bound,
    summarize_audit,
)
from .audit_runs import _audit_run_row as _audit_run_row  # noqa: F401
from .audit_runs import (
    abort_audit_run,
    audit_run_exists_for_key,
    consume_audit_run_validation_reserve,
    create_audit_run,
    delete_audit_run,
    extend_audit_run_total,
    get_audit_run,
    insert_audit_parity,
    list_audit_run_jobs,
    list_audit_run_parity,
    list_audit_runs,
    list_failed_audit_run_jobs,
    set_audit_run_total,
)
from .audit_schedules import _SCHEDULE_COLS as _SCHEDULE_COLS  # noqa: F401
from .audit_schedules import _audit_schedule_row as _audit_schedule_row  # noqa: F401
from .audit_schedules import (
    archive_audit_schedule,
    claim_due_audit_schedule,
    create_audit_schedule,
    get_audit_schedule,
    list_audit_schedules,
    set_audit_schedule_skip_reason,
    update_audit_schedule,
)
from .catalogs import _equipment_row_summary as _equipment_row_summary  # noqa: F401
from .catalogs import _system_row_summary as _system_row_summary  # noqa: F401
from .catalogs import (
    apply_inferred_bbox,
    archive_equipment_type,
    archive_system_template,
    create_equipment_type,
    create_system_template,
    get_catalog_fingerprint,
    get_equipment_cad_keys_by_scope,
    get_equipment_docs_by_scope,
    get_equipment_type,
    get_system_template,
    list_equipment_types,
    list_system_templates,
    set_equipment_type_cad,
    update_equipment_type,
    update_system_template,
)
from .corpora import _corpus_row as _corpus_row  # noqa: F401
from .corpora import (
    archive_corpus,
    create_corpus,
    get_corpus_by_slug,
    list_corpora,
    update_corpus,
)
from .issue_bot import (
    audit_log_history_for_cell,
    claim_audit_run_for_auto_validate,
    claim_audit_run_for_issue_bot,
    claim_failed_conversion_for_issue_bot,
    claim_run_for_validation,
    mark_audit_log_issue_bot,
    mark_audit_run_issue_bot,
    reset_audit_log_issue_bot,
    reset_audit_run_issue_bot,
)
from .metrics import (
    aggregate_conversion_metrics,
    aggregate_render_metrics,
    aggregate_view_load_hotspots,
    aggregate_view_load_metrics,
    get_audit_client_metrics,
)
from .migrations import _MIGRATION_LOCK_ID as _MIGRATION_LOCK_ID  # noqa: F401
from .migrations import _apply_migrations as _apply_migrations  # noqa: F401
from .plugin_jobs import (  # noqa: F401
    _PLUGIN_JOB_SCHEDULE_COLS as _PLUGIN_JOB_SCHEDULE_COLS,
)
from .plugin_jobs import (  # noqa: F401
    _plugin_job_schedule_row as _plugin_job_schedule_row,
)
from .plugin_jobs import (
    archive_plugin_job_schedule,
    claim_due_plugin_job_schedule,
    create_plugin_job_schedule,
    get_plugin_job_schedule,
    list_plugin_job_schedules,
    plugin_job_in_flight_jobs,
    set_plugin_job_schedule_skip_reason,
    update_plugin_job_schedule,
)
from .pool import close_pool, init_pool
from .procedural_models import _engine_row_summary as _engine_row_summary  # noqa: F401
from .procedural_models import (  # noqa: F401
    _procedural_row_summary as _procedural_row_summary,
)
from .procedural_models import (
    archive_procedural_engine,
    archive_procedural_model,
    create_procedural_engine,
    create_procedural_model,
    get_procedural_engine,
    get_procedural_engine_by_slug,
    get_procedural_model,
    list_procedural_engines,
    list_procedural_models,
    rename_procedural_model,
    set_procedural_engine_wheel,
    update_procedural_engine,
    update_procedural_model_doc,
)
from .profiles import (
    aggregate_profile_hotspots,
    claim_unprocessed_profile_row,
    insert_profile_function_stats,
    mark_profile_stats_failed,
)
from .projects import (
    Project,
    add_project_member,
    archive_project,
    create_project,
    is_project_member,
    list_all_projects,
    list_project_members,
    list_user_projects,
    project_exists,
    project_id_from_slug,
    remove_project_member,
    upsert_user,
)
from .settings import get_setting, set_setting
from .source_nodes import SourceNode
from .source_nodes import _source_node_row as _source_node_row  # noqa: F401
from .source_nodes import (
    get_source_node,
    get_source_nodes,
    list_source_node_sources,
    list_source_nodes_changed_since,
    record_source_nodes,
)
from .workers import get_worker_packages, upsert_worker_packages

__all__ = [
    "init_pool",
    "close_pool",
    "Project",
    "upsert_user",
    "list_user_projects",
    "is_project_member",
    "list_all_projects",
    "create_project",
    "archive_project",
    "list_project_members",
    "add_project_member",
    "remove_project_member",
    "project_exists",
    "project_id_from_slug",
    "insert_audit",
    "cancel_audit_by_job",
    "admin_cancel_audit_by_job",
    "audit_is_cancelled",
    "get_audit_owner_by_job",
    "mark_audit_running",
    "update_audit_by_job",
    "reset_audit_cell_for_rerun",
    "active_audit_summary",
    "append_metrics_sample_by_job",
    "parse_audit_time_bound",
    "list_audit",
    "summarize_audit",
    "get_audit_by_job",
    "get_audit_by_id",
    "clear_audit_metrics",
    "upsert_worker_packages",
    "get_worker_packages",
    "get_setting",
    "set_setting",
    "list_corpora",
    "create_corpus",
    "update_corpus",
    "get_corpus_by_slug",
    "archive_corpus",
    "abort_audit_run",
    "create_audit_run",
    "set_audit_run_total",
    "extend_audit_run_total",
    "consume_audit_run_validation_reserve",
    "delete_audit_run",
    "list_audit_runs",
    "get_audit_run",
    "list_audit_run_jobs",
    "insert_audit_parity",
    "list_audit_run_parity",
    "audit_run_exists_for_key",
    "list_failed_audit_run_jobs",
    "list_audit_schedules",
    "get_audit_schedule",
    "create_audit_schedule",
    "update_audit_schedule",
    "archive_audit_schedule",
    "claim_due_audit_schedule",
    "set_audit_schedule_skip_reason",
    "claim_audit_run_for_issue_bot",
    "claim_audit_run_for_auto_validate",
    "claim_run_for_validation",
    "audit_log_history_for_cell",
    "mark_audit_run_issue_bot",
    "reset_audit_run_issue_bot",
    "claim_failed_conversion_for_issue_bot",
    "mark_audit_log_issue_bot",
    "reset_audit_log_issue_bot",
    "aggregate_conversion_metrics",
    "aggregate_view_load_metrics",
    "aggregate_view_load_hotspots",
    "aggregate_render_metrics",
    "get_audit_client_metrics",
    "claim_unprocessed_profile_row",
    "insert_profile_function_stats",
    "mark_profile_stats_failed",
    "aggregate_profile_hotspots",
    "create_procedural_model",
    "rename_procedural_model",
    "list_procedural_models",
    "get_procedural_model",
    "update_procedural_model_doc",
    "archive_procedural_model",
    "create_procedural_engine",
    "list_procedural_engines",
    "get_procedural_engine",
    "update_procedural_engine",
    "get_procedural_engine_by_slug",
    "set_procedural_engine_wheel",
    "archive_procedural_engine",
    "create_equipment_type",
    "list_equipment_types",
    "get_equipment_docs_by_scope",
    "get_equipment_cad_keys_by_scope",
    "get_catalog_fingerprint",
    "get_equipment_type",
    "update_equipment_type",
    "set_equipment_type_cad",
    "apply_inferred_bbox",
    "archive_equipment_type",
    "create_system_template",
    "list_system_templates",
    "get_system_template",
    "update_system_template",
    "archive_system_template",
    "SourceNode",
    "record_source_nodes",
    "get_source_node",
    "get_source_nodes",
    "list_source_nodes_changed_since",
    "list_source_node_sources",
    "list_plugin_job_schedules",
    "get_plugin_job_schedule",
    "create_plugin_job_schedule",
    "update_plugin_job_schedule",
    "archive_plugin_job_schedule",
    "claim_due_plugin_job_schedule",
    "set_plugin_job_schedule_skip_reason",
    "plugin_job_in_flight_jobs",
]
