"""Per-job conversion settings: the admin app_settings knobs (read fresh from the DB per job)
merged with the job's own ``conversion_options`` overrides, resolved to the cProfile toggle, the
ADA_* env overrides applied inside the convert child fork, and the optional wall-clock budget.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import asyncpg

from ada.config import logger

from .. import db as db_module
from ..queue import Job


@dataclass
class ConversionSettings:
    """What the convert subprocess runs with (see :func:`read_conversion_settings`)."""

    profile_enabled: bool = False
    env_overrides: dict[str, str] = field(default_factory=dict)
    timeout_s: float | None = None


async def read_conversion_settings(db_pool: asyncpg.Pool | None, job: Job) -> ConversionSettings:
    """Read the conversion settings for ``job``.

    Conversion settings flip via the admin panel and are read fresh per job — admins can flip one
    on, send a representative job, and flip it off without a worker restart. No cache: one DB
    round-trip per setting is negligible next to a tessellation pass.

    ``profile_conversions`` toggles cProfile inside the fork-child and is consumed directly. The
    others are mapped to ADA_* env vars and applied inside the child fork only, so sibling jobs /
    the parent worker keep their pristine env. Per-job ``conversion_options`` win over the global
    settings (``None`` clears an env var).
    """
    profile_enabled = False
    env_overrides: dict[str, str] = {}
    # Initialised here (not only inside the ``db_pool is not None`` block below) so a worker
    # that came up without a DB pool — e.g. it raced Postgres during a restart — still converts
    # with code defaults instead of crashing every job with UnboundLocalError on ``timeout_s``.
    timeout_s: float | None = None
    if db_pool is not None:

        async def _read_bool_setting(key: str) -> str | None:
            try:
                return await db_module.get_setting(db_pool, key)
            except Exception:
                logger.exception("worker: failed to read %s setting", key)
                return None

        v = await _read_bool_setting("profile_conversions")
        profile_enabled = (v or "").strip().lower() in {"1", "true", "yes", "on"}
        # Per-task profiling filter (admin key `profile_task_types`, a
        # comma-separated target_format list; empty = all tasks). Gates the
        # toggle so an admin can profile only e.g. `glb` or `plugin_job`
        # without a per-job override. Same key the plugin_job harness reads.
        _ptt = await _read_bool_setting("profile_task_types")
        _allowed_types = {t.strip() for t in (_ptt or "").split(",") if t.strip()}
        profile_enabled = profile_enabled and (not _allowed_types or job.target_format in _allowed_types)
        if profile_enabled:
            # The C++ sibling of the cProfile artefact: adacpp's env-gated
            # [STEPPROF] pipeline profiler (phase wall times, RSS at phase
            # boundaries, VmHWM peak, per-solid stats, parallelism/IO
            # pressure) prints to stderr, which the captured job Log keeps.
            # Applied inside the child fork only, so sibling jobs and the
            # parent worker keep their pristine env.
            env_overrides["ADACPP_STEP_PROFILE"] = "1"

        # Optional per-job wall-clock budget. Empty / 0 / non-
        # numeric leaves the watchdog off so legitimately-long
        # bakes (a multi-GiB FEA result sweep can take 20+ min)
        # aren't artificially killed. Set as a positive minutes
        # value to enable; the parent process then SIGTERMs the
        # convert subprocess after the deadline and SIGKILLs
        # 30 s later if it's still alive.
        timeout_minutes_raw = await _read_bool_setting("conversion_timeout_minutes")
        try:
            tm = float((timeout_minutes_raw or "").strip())
            if tm > 0:
                timeout_s = tm * 60.0
        except (TypeError, ValueError):
            timeout_s = None

        # setting key → env var name. Worker passes the raw
        # truthy/falsy text through; surfaces.py /
        # converter.py do the same parsing they always have, so
        # the env-driven and admin-driven paths agree on edge
        # cases (e.g. "yes" / "no").
        _env_map = {
            "use_sat_pcurves": "ADA_USE_SAT_PCURVES",
            "skip_shapefix": "ADA_SKIP_SHAPEFIX",
            "merge_meshes": "ADA_GLB_MERGE_MESHES",
            # Reuse a parsed source across export targets: parse once, pickle (content-hashed,
            # local), reuse for every other target instead of re-reading the file. Big win for
            # audit runs (one source → many targets); harmless when a source converts once.
            "assembly_cache": "ADA_ASSEMBLY_CACHE",
            # STEP→GLB tessellation engine (libtess2 / occ-builtin / step2glb /
            # adacpp-{occ,cgal,hybrid}); enum string, read by _resolve_step_glb_pipeline.
            "step_glb_pipeline": "ADAPY_STEP_GLB_PIPELINE",
            # STEP→GLB streaming defaults (large-file OOM guard).
            "step_streamer_auto": "ADA_STEP_STREAMER_AUTO",
            "step_streamer_threshold_mb": "ADA_STEP_STREAMER_THRESHOLD_MB",
            # Per-solid tessellation budget; a solid that overruns it (OCC hang) is
            # killed and skipped so one bad solid can't freeze the whole conversion.
            "step_stream_solid_timeout_s": "ADA_STEP_STREAM_SOLID_TIMEOUT_S",
            # STEP→GLB tessellation pool memory bound: worker count cap + per-worker
            # soft/hard RSS caps (a worker over soft respawns between solids; over hard
            # mid-solid is killed + the solid requeued once). Sizes peak conversion RSS.
            "step_stream_workers": "ADA_STEP_STREAM_WORKERS",
            "step_stream_worker_soft_mem_mb": "ADA_STEP_STREAM_WORKER_SOFT_MEM_MB",
            "step_stream_worker_hard_mem_mb": "ADA_STEP_STREAM_WORKER_HARD_MEM_MB",
            # FEM→IFC memory-bounded writer. Default on (converter treats
            # unset as on); set falsy to revert to the in-memory writer.
            "ifc_streaming": "ADA_IFC_STREAMING",
            # Curved-surface tessellation quality (0 = lean relative default).
            "tess_linear_deflection": "ADA_OCC_TESS_LINEAR_DEFLECTION",
            "tess_angular_deg": "ADA_OCC_TESS_ANGULAR_DEG",
            "tess_relative": "ADA_OCC_TESS_RELATIVE",
            # Conversion log verbosity (DEBUG/INFO/WARNING/ERROR), set from the admin Conversion
            # panel. Unset keeps the quiet WARNING default; INFO surfaces per-stage progress + the
            # native engine summary in the captured audit Log. Read by the convert subprocess.
            "convert_log_level": "ADA_CONVERT_LOG_LEVEL",
        }
        for skey, env_name in _env_map.items():
            raw = await _read_bool_setting(skey)
            if raw is not None and raw.strip() != "":
                env_overrides[env_name] = raw

        # Per-source-type tessellation engine. STEP→glb and the scene path
        # (gxml/ifc/sat→glb via to_gltf's BatchTessellator) use different engine envs;
        # resolve the one for THIS source's type from its own setting so e.g. gxml can run
        # libtess2 while ifc runs occ. Unset → the converter's adacpp-aware default (the scene
        # path defaults to libtess2 when adacpp is present; OCC's prism tessellation of curved
        # B-spline plates is non-manifold and drops the viewer's edge outlines). A per-type
        # setting here supersedes the legacy single ``step_glb_pipeline`` for STEP sources.
        _tess_engine_by_ext = {
            ".step": ("tess_engine_step", "ADAPY_STEP_GLB_PIPELINE"),
            ".stp": ("tess_engine_step", "ADAPY_STEP_GLB_PIPELINE"),
            ".xml": ("tess_engine_gxml", "ADAPY_GLB_TESS_ENGINE"),
            ".ifc": ("tess_engine_ifc", "ADAPY_GLB_TESS_ENGINE"),
            ".sat": ("tess_engine_sat", "ADAPY_GLB_TESS_ENGINE"),
            ".acis": ("tess_engine_sat", "ADAPY_GLB_TESS_ENGINE"),
        }
        _src_ext = os.path.splitext(job.source_key)[1].lower()
        _eng = _tess_engine_by_ext.get(_src_ext)
        if _eng is not None:
            _skey, _engine_env = _eng
            _raw_engine = await _read_bool_setting(_skey)
            if _raw_engine is not None and _raw_engine.strip() != "":
                env_overrides[_engine_env] = _raw_engine.strip()

    # Per-job overrides win over global settings. ``None`` clears
    # an env var, allowing a job to ask "ignore the global
    # toggle, run with adapy's code default" without restarting.
    per_job = getattr(job, "conversion_options", None) or {}
    if per_job:
        _env_map_full = {
            "use_sat_pcurves": "ADA_USE_SAT_PCURVES",
            "skip_shapefix": "ADA_SKIP_SHAPEFIX",
            "merge_meshes": "ADA_GLB_MERGE_MESHES",
            "assembly_cache": "ADA_ASSEMBLY_CACHE",
            "step_glb_pipeline": "ADAPY_STEP_GLB_PIPELINE",
            "step_streamer": "ADA_STEP_STREAMER",
            "ifc_streaming": "ADA_IFC_STREAMING",
            "tess_linear_deflection": "ADA_OCC_TESS_LINEAR_DEFLECTION",
            "tess_angular_deg": "ADA_OCC_TESS_ANGULAR_DEG",
            "tess_relative": "ADA_OCC_TESS_RELATIVE",
            # Per-face pick regions in the GLB (face_ranges_node in scene extras). Per-JOB
            # only, deliberately: it enlarges the GLB and forces serial face tessellation, so
            # it is a debugging ask for one conversion, never a deployment-wide setting.
            # Honoured by the native STEP->GLB path alone — which is why the API advertises it
            # with supported_by=[cpp] rather than offering it against every serializer.
            "face_regions": "ADA_STREAM_TESS_FACE_REGIONS",
        }
        for k, v in per_job.items():
            env_name = _env_map_full.get(k)
            if env_name is None:
                continue
            if v is None:
                env_overrides.pop(env_name, None)
            else:
                env_overrides[env_name] = str(v)
        # profile is passed as a kwarg to run_isolated_convert
        # rather than as an env var.
        if "profile_conversions" in per_job and per_job["profile_conversions"] is not None:
            profile_enabled = str(per_job["profile_conversions"]).strip().lower() in {"1", "true", "yes", "on"}

    return ConversionSettings(profile_enabled=profile_enabled, env_overrides=env_overrides, timeout_s=timeout_s)
