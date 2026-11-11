// ConnectionBuilder — the studio side of `ada dev connections`, the Scene panel's
// Connection tab: pick a spec from the served connection library, edit each member
// (a beam's section, a plate's thickness and extent, the angle between them) and see
// the joint rebuilt, on every edit here and on every saved change to the library's
// files. Under the inputs, the joint that was built: its clash (also loaded into
// Scene > Clashes), its members and its welds -- each selectable in the scene.
//
// State lives in connectionBuilderStore; the live loop (event stream, debounced
// builds, scene loading) in services/devConnections/connectionBuilderSession.

import React, { useEffect, useMemo, useState } from "react";

import CollapsibleSection from "@/components/common/CollapsibleSection";
import type { ComponentSpecRoleSchema } from "@/services/api/components";
import type { DevBuildResult, DevConnectionSpec } from "@/services/api/devConnections";
import {
  reloadLibrary,
  requestBuild,
  restartLibrary,
  selectPreviewObjects,
  startConnectionBuilderSession,
} from "@/services/devConnections/connectionBuilderSession";
import {
  type ConnectionBuildState,
  effectiveInputs,
  useConnectionBuilderStore,
} from "@/state/connectionBuilderStore";
import { useSceneInfoStore } from "@/state/sceneInfoStore";
import { weldTypeName } from "@/utils/weldTypeNames";

/** Sections offered per `section_in` family; any section adapy can parse may be typed. */
const SECTION_SUGGESTIONS: Record<string, string[]> = {
  BOX: ["BOX200x200x10x10", "BOX300x300x12x12", "BOX400x400x16x16", "BOX300x200x10x10"],
  TUB: ["TUB150x8", "TUB200x10", "TUB300x10", "TUB400x12"],
  TUBULAR: ["TUB150x8", "TUB200x10", "TUB300x10", "TUB400x12"],
  I: ["IPE200", "IPE300", "IPE400", "HEA300", "HEB300"],
  IPROFILE: ["IPE200", "IPE300", "IPE400", "HEA300", "HEB300"],
  UNP: ["UNP160", "UNP200", "UNP260"],
  FB: ["FB100x10", "FB200x20"],
};

const input = "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 min-w-0";

/** The connection builder's content: library status, spec picker, member inputs, build result. */
export const ConnectionBuilderBody: React.FC = () => {
  useEffect(() => startConnectionBuilderSession(), []);
  const library = useConnectionBuilderStore((s) => s.library);
  const connected = useConnectionBuilderStore((s) => s.connected);
  const selectedSpec = useConnectionBuilderStore((s) => s.selectedSpec);
  const selectSpec = useConnectionBuilderStore((s) => s.selectSpec);
  const inputsBySpec = useConnectionBuilderStore((s) => s.inputsBySpec);
  const setRoleField = useConnectionBuilderStore((s) => s.setRoleField);
  const resetInputs = useConnectionBuilderStore((s) => s.resetInputs);
  const build = useConnectionBuilderStore((s) => s.build);
  const lastResult = useConnectionBuilderStore((s) => s.lastResult);

  const spec: DevConnectionSpec | null = useMemo(
    () => library?.specs.find((x) => x.name === selectedSpec) ?? null,
    [library, selectedSpec],
  );

  if (!library) {
    return <div className="text-gray-400">{connected ? "Loading the connection library…" : "Connecting…"}</div>;
  }

  const own = library.specs.filter((x) => x.in_library);
  const imported = library.specs.filter((x) => !x.in_library);
  const values = spec ? effectiveInputs(spec.defaults, inputsBySpec[spec.name]) : {};

  return (
    <div className="flex flex-col gap-2 text-xs">
      <LibraryHeader />
      {library.status === "error" && library.error && (
        <ErrorBox title="The library did not load" error={library.error.error} traceback={library.error.traceback} />
      )}

      <CollapsibleSection title="Inputs" defaultOpen>
      <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-gray-300 shrink-0">Connection</span>
        <select
          className={`${input} flex-1 truncate`}
          value={selectedSpec ?? ""}
          onChange={(e) => selectSpec(e.target.value || null)}
          disabled={library.specs.length === 0}
        >
          {library.specs.length === 0 && <option value="">— no specs registered —</option>}
          {own.length > 0 && (
            <optgroup label={library.library.name}>
              {own.map((x) => (
                <option key={x.name} value={x.name}>
                  {x.name}
                </option>
              ))}
            </optgroup>
          )}
          {imported.length > 0 && (
            <optgroup label="other registered specs">
              {imported.map((x) => (
                <option key={x.name} value={x.name}>
                  {x.name}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </div>

      {spec && (
        <>
          {spec.source && (
            <div className="text-gray-500 truncate" title={spec.source.file}>
              {shortPath(spec.source.file, library.library.root)}:{spec.source.line}
            </div>
          )}
          {spec.schema.roles.map((role) => (
            <RoleEditor
              key={role.role}
              role={role}
              value={values[role.role] ?? {}}
              onField={(field, value) => setRoleField(spec.name, role.role, field, value)}
            />
          ))}
          <div className="flex items-center gap-2 pt-1 border-t border-gray-700">
            <button
              type="button"
              className="bg-blue-600 hover:bg-blue-500 disabled:bg-gray-600 text-white rounded-sm px-3 py-1"
              onClick={() => requestBuild(0)}
              disabled={library.status !== "ready"}
              title="Rebuild now (edits and saved changes rebuild on their own)"
            >
              Rebuild
            </button>
            <button
              type="button"
              className="bg-gray-700 hover:bg-gray-600 text-white rounded-sm px-3 py-1"
              onClick={() => resetInputs(spec.name)}
              disabled={!inputsBySpec[spec.name]}
              title="Back to the spec's defaults"
            >
              Defaults
            </button>
          </div>
          <BuildStatus build={build} specName={spec.name} />
        </>
      )}
      </div>
      </CollapsibleSection>

      {/* The last joint built for this spec stays up while the next one builds. */}
      {spec && lastResult?.spec_name === spec.name && <JointSections result={lastResult} />}
    </div>
  );
};

const LibraryHeader: React.FC = () => {
  const library = useConnectionBuilderStore((s) => s.library)!;
  const connected = useConnectionBuilderStore((s) => s.connected);
  const [busy, setBusy] = useState(false);
  const run = (fn: () => Promise<void>) => async () => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      console.error("connection library action failed", err);
    } finally {
      setBusy(false);
    }
  };
  const status = connected ? library.status : "disconnected";
  const color =
    status === "ready" ? "bg-green-700" : status === "error" || status === "disconnected" ? "bg-red-700" : "bg-yellow-700";
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <span className="font-medium truncate" title={library.library.config}>
          {library.library.name}
        </span>
        <span className={`${color} rounded px-1.5 text-[10px] uppercase tracking-wide`}>{status}</span>
        <span className="flex-1" />
        <button
          type="button"
          className="text-gray-300 hover:text-white disabled:text-gray-600"
          onClick={run(reloadLibrary)}
          disabled={busy || !connected}
          title="Reload the library now"
        >
          Reload
        </button>
        <button
          type="button"
          className="text-gray-300 hover:text-white disabled:text-gray-600"
          onClick={run(restartLibrary)}
          disabled={busy || !connected}
          title="Start a fresh library process (re-reads ada_connections.toml)"
        >
          Restart
        </button>
      </div>
      {library.changed.length > 0 && (
        <div className="text-gray-500 truncate" title={library.changed.join("\n")}>
          reloaded after: {library.changed.join(", ")}
        </div>
      )}
    </div>
  );
};

const RoleEditor: React.FC<{
  role: ComponentSpecRoleSchema;
  value: Record<string, unknown>;
  onField: (field: string, value: unknown) => void;
}> = ({ role, value, onField }) => {
  const fields = role.fields ?? (role.kind === "PLATE" ? ["thickness", "width", "height"] : ["section"]);
  return (
    <div className="flex flex-col gap-1 p-1 bg-gray-800/40 rounded-sm">
      <div className="text-gray-300 font-medium">
        {role.role}
        {role.kind ? <span className="text-gray-500"> · {role.kind.toLowerCase()}</span> : null}
        {role.section_in && role.kind !== "PLATE" ? (
          <span className="text-gray-500"> · {role.section_in.join(" | ")}</span>
        ) : null}
      </div>
      {fields.includes("section") && (
        <SectionField role={role} value={value.section as string | undefined} onCommit={(v) => onField("section", v)} />
      )}
      {(["thickness", "width", "height"] as const)
        .filter((f) => fields.includes(f))
        .map((f) => (
          <MillimetreField key={f} label={f} metres={value[f] as number | undefined} onCommit={(m) => onField(f, m)} />
        ))}
      {fields.includes("angle_deg") && (
        <AngleField role={role} value={value.angle_deg as number | undefined} onCommit={(v) => onField("angle_deg", v)} />
      )}
      {fields.includes("corner_radius") && <BeamOptions value={value} onField={onField} />}
    </div>
  );
};

/** A beam's further options, folded away until wanted: a box's outer and inner corner radii. */
const BeamOptions: React.FC<{
  value: Record<string, unknown>;
  onField: (field: string, value: unknown) => void;
}> = ({ value, onField }) => {
  const set = typeof value.corner_radius === "number" || typeof value.corner_radius_inner === "number";
  const [open, setOpen] = useState(set);
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        className="self-start text-gray-400 hover:text-white"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? "▾" : "▸"} Options{!open && set ? " · rounded corners" : ""}
      </button>
      {open && (
        <div className="flex flex-col gap-1 pl-3">
          <OptionalMillimetreField
            label="corner r"
            title="Outer corner radius of a box section (RADA). Empty: sharp corners."
            metres={value.corner_radius as number | null | undefined}
            onCommit={(m) => onField("corner_radius", m)}
          />
          <OptionalMillimetreField
            label="inner r"
            title="Inner corner radius. Empty: the outer radius less the wall (a cold-formed section); a hot-finished one (EN 10210) is 1.0 t inside, 1.5 t outside."
            metres={value.corner_radius_inner as number | null | undefined}
            onCommit={(m) => onField("corner_radius_inner", m)}
          />
        </div>
      )}
    </div>
  );
};

/** A length in millimetres that may be left empty (null). */
const OptionalMillimetreField: React.FC<{
  label: string;
  title: string;
  metres: number | null | undefined;
  onCommit: (m: number | null) => void;
}> = ({ label, title, metres, onCommit }) => {
  const shown = typeof metres === "number" ? String(Math.round(metres * 1e4) / 10) : "";
  const [draft, setDraft] = useState(shown);
  useEffect(() => setDraft(shown), [shown]);
  const commit = (text: string) => {
    if (!text.trim()) return onCommit(null);
    const mm = parseFloat(text);
    if (Number.isFinite(mm) && mm >= 0) onCommit(mm > 0 ? mm / 1000 : null);
  };
  return (
    <label className="flex items-center gap-2" title={title}>
      <span className="text-gray-400 w-16 shrink-0">{label}</span>
      <input
        type="number"
        min={0}
        step={1}
        placeholder="—"
        className={`${input} w-24`}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => commit(draft)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(draft);
        }}
      />
      <span className="text-gray-500">mm</span>
    </label>
  );
};

/** Free text with suggestions; committed on Enter, blur, or picking a suggestion, not per keystroke. */
const SectionField: React.FC<{
  role: ComponentSpecRoleSchema;
  value: string | undefined;
  onCommit: (v: string) => void;
}> = ({ role, value, onCommit }) => {
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  const suggestions = useMemo(() => {
    const out = new Set<string>(value ? [value] : []);
    for (const fam of role.section_in ?? Object.keys(SECTION_SUGGESTIONS)) {
      for (const s of SECTION_SUGGESTIONS[fam.toUpperCase()] ?? []) out.add(s);
    }
    return [...out];
  }, [role.section_in, value]);
  const listId = `connection-builder-sections-${role.role}`;
  const commit = (v: string) => {
    const t = v.trim();
    if (t && t !== value) onCommit(t);
  };
  return (
    <label className="flex items-center gap-2">
      <span className="text-gray-400 w-16 shrink-0">section</span>
      <input
        className={`${input} flex-1`}
        list={listId}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          if (suggestions.includes(e.target.value)) commit(e.target.value);
        }}
        onBlur={() => commit(draft)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(draft);
          if (e.key === "Escape") setDraft(value ?? "");
        }}
      />
      <datalist id={listId}>
        {suggestions.map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
    </label>
  );
};

/** A length shown and typed in millimetres, stored in metres (what adapy builds with). */
const MillimetreField: React.FC<{
  label: string;
  metres: number | undefined;
  onCommit: (m: number) => void;
}> = ({ label, metres, onCommit }) => {
  const shown = metres === undefined ? "" : String(Math.round(metres * 1e4) / 10);
  const [draft, setDraft] = useState(shown);
  useEffect(() => setDraft(shown), [shown]);
  return (
    <label className="flex items-center gap-2">
      <span className="text-gray-400 w-16 shrink-0">{label}</span>
      <input
        type="number"
        min={0}
        step={1}
        className={`${input} w-24`}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          const mm = parseFloat(e.target.value);
          if (Number.isFinite(mm) && mm > 0) onCommit(mm / 1000);
        }}
        onBlur={() => setDraft(shown)}
      />
      <span className="text-gray-500">mm</span>
    </label>
  );
};

const AngleField: React.FC<{
  role: ComponentSpecRoleSchema;
  value: number | undefined;
  onCommit: (v: number) => void;
}> = ({ role, value, onCommit }) => {
  const min = role.angle_range?.min_deg ?? 0;
  const max = role.angle_range?.max_deg ?? 180;
  const current = typeof value === "number" ? value : 90;
  const set = (v: number) => {
    if (Number.isFinite(v)) onCommit(Math.min(max, Math.max(min, v)));
  };
  return (
    <label className="flex items-center gap-2">
      <span className="text-gray-400 w-16 shrink-0">angle°</span>
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={current}
        onChange={(e) => set(parseFloat(e.target.value))}
        className="flex-1 h-2 rounded-lg appearance-none cursor-pointer accent-blue-700 bg-blue-700/30 min-w-0"
      />
      <input
        type="number"
        className={`${input} w-16`}
        min={min}
        max={max}
        value={current}
        onChange={(e) => set(parseFloat(e.target.value))}
      />
    </label>
  );
};

/** A build in flight: a spinner, the seconds so far and, once this spec has built before, a bar
 *  against how long that took (the server reports no stages, so it is an estimate, held at 95%). */
const BuildProgress: React.FC<{
  build: Extract<ConnectionBuildState, { status: "building" }>;
  specName: string;
}> = ({ build, specName }) => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(id);
  }, []);
  const elapsed = Math.max(0, now - build.startedAt);
  const fraction = build.expectedMs ? Math.min(0.95, elapsed / build.expectedMs) : null;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 text-gray-300">
        <span className="inline-block w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin shrink-0" />
        <span>Building {build.specName === specName ? "" : build.specName}…</span>
        <span className="text-gray-500 tabular-nums">
          {(elapsed / 1000).toFixed(1)} s{build.expectedMs ? ` / ~${(build.expectedMs / 1000).toFixed(1)} s` : ""}
        </span>
      </div>
      {fraction !== null && (
        <div className="h-1 rounded bg-blue-700/30 overflow-hidden">
          <div className="h-full bg-blue-500 transition-[width] duration-100" style={{ width: `${fraction * 100}%` }} />
        </div>
      )}
    </div>
  );
};

const BuildStatus: React.FC<{ build: ConnectionBuildState; specName: string }> = ({ build, specName }) => {
  if (build.status === "idle") return null;
  if (build.status === "building") return <BuildProgress build={build} specName={specName} />;
  if (build.status === "error" && build.declined) {
    // NotApplicable: the builder refused these sample members -- its reason is the message.
    return (
      <ErrorBox
        tone="notice"
        title={`${build.specName} declined`}
        error={build.error.replace(/^\w*NotApplicable:\s*/, "")}
        hint="The builder raised NotApplicable for these members: its preconditions are not met by the sample members (a spec can build its own with `synth_sample`)."
        traceback={build.traceback}
      />
    );
  }
  if (build.status === "error") {
    return <ErrorBox title={`${build.specName} failed`} error={build.error} traceback={build.traceback} />;
  }
  const { result } = build;
  const counts = Object.entries(result.counts)
    .filter(([, n]) => n)
    .map(([k, n]) => `${n} ${n === 1 ? k.replace(/s$/, "") : k}`)
    .join(" · ");
  const warnings = result.log.filter((l) => l.level !== "INFO");
  return (
    <div className="flex flex-col gap-1">
      <div className="text-green-400">
        Built in {result.build_ms} ms{counts ? ` · ${counts}` : ""}
      </div>
      {warnings.length > 0 && (
        <ul className="flex flex-col gap-0.5 max-h-32 overflow-auto">
          {warnings.map((l, i) => (
            <li key={i} className={l.level === "WARNING" ? "text-yellow-300" : "text-red-300"} title={l.logger}>
              {l.level.toLowerCase()}: {l.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

/** A failure (red, traceback open) or a notice such as a declined build (amber, traceback folded). */
const ErrorBox: React.FC<{
  title: string;
  error: string;
  traceback: string | null;
  tone?: "error" | "notice";
  hint?: string;
}> = ({ title, error, traceback, tone = "error", hint }) => {
  const notice = tone === "notice";
  const [open, setOpen] = useState(!notice);
  return (
    <div
      className={`flex flex-col gap-1 p-1 border rounded-sm ${
        notice ? "border-yellow-800 bg-yellow-950/40" : "border-red-800 bg-red-950/50"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className={`${notice ? "text-yellow-200" : "text-red-300"} font-medium flex-1 min-w-0 break-words`}>
          {title}: {error}
        </span>
        {traceback && (
          <button type="button" className="text-gray-400 hover:text-white shrink-0" onClick={() => setOpen(!open)}>
            {open ? "hide" : "traceback"}
          </button>
        )}
      </div>
      {hint && <div className="text-gray-400">{hint}</div>}
      {traceback && open && (
        <pre className="text-[10px] leading-tight text-red-200 whitespace-pre overflow-auto max-h-64">{traceback}</pre>
      )}
    </div>
  );
};

function shortPath(file: string, root: string): string {
  const norm = (p: string) => p.replace(/\\/g, "/");
  const f = norm(file);
  const r = norm(root).replace(/\/+$/, "");
  return f.toLowerCase().startsWith(r.toLowerCase() + "/") ? f.slice(r.length + 1) : f;
}

// ── the joint that was built: its clash, its members, its welds ─────────────────────────────

const fmt = (v: number | null | undefined, digits = 3) => (typeof v === "number" ? v.toFixed(digits) : "–");
const mm = (m: number | null | undefined) => (typeof m === "number" ? `${Math.round(m * 10000) / 10} mm` : "–");
const vec = (v: readonly number[] | undefined) => (v ? `(${v.map((c) => c.toFixed(2)).join(", ")})` : "–");

/** The latest successful build's joint, as collapsible sections under the inputs. */
const JointSections: React.FC<{ result: DevBuildResult }> = ({ result }) => (
  <>
    <CollapsibleSection title="Clash" defaultOpen>
      <ClashSummary result={result} />
    </CollapsibleSection>
    <CollapsibleSection title={`Members (${result.properties.members.length})`} defaultOpen>
      <MembersList result={result} />
    </CollapsibleSection>
    <CollapsibleSection title={`Welds (${result.properties.welds.length})`} defaultOpen>
      <WeldsList result={result} />
    </CollapsibleSection>
  </>
);

/** The joint's measured contact -- what a clash check finds where the members meet -- and the
 *  specs that claim it. The full run is in Scene > Clashes, with its patches and normals in 3D. */
const ClashSummary: React.FC<{ result: DevBuildResult }> = ({ result }) => {
  const run = result.clash_run;
  if (!run) {
    return <div className="text-gray-400">Not measured{result.clash_note ? `: ${result.clash_note}` : "."}</div>;
  }
  if (run.contacts.length === 0) {
    return <div className="text-yellow-300">The members do not touch: no contact between them in the built joint.</div>;
  }
  const nameOf = (k: number) => run.members[k]?.name ?? `#${k}`;
  return (
    <div className="flex flex-col gap-1.5">
      {run.contacts.map((c) => (
        <div key={c.id} className="flex flex-col gap-0.5 p-1 bg-gray-800/40 rounded-sm">
          <div className="text-gray-200">
            {c.roles ? (
              <>
                <span className="text-amber-300">{nameOf(c.roles.incoming)}</span> lands on{" "}
                <span className="text-teal-300">{nameOf(c.roles.landing)}</span>
              </>
            ) : (
              `${nameOf(c.members[0])} ↔ ${nameOf(c.members[1])}`
            )}
          </div>
          <Row label="contact area" value={`${fmt(c.contact_area * 1e4, 1)} cm²`} />
          <Row label="normal" value={vec(c.normal)} />
          <Row label={c.distance < 0 ? "overlap" : "gap"} value={mm(Math.abs(c.distance))} />
          <Row label="patches" value={String(c.patches.length)} />
          <Row
            label="claimed by"
            value={
              c.applicable?.length
                ? [...c.applicable].sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0)).map((a) => a.spec).join(", ")
                : "no spec"
            }
          />
        </div>
      ))}
      <button
        type="button"
        className="self-start text-blue-300 hover:text-blue-200"
        onClick={() => useSceneInfoStore.getState().setMode("clashes")}
        title="The run in Scene > Clashes: select the clash, show its contact patches and normals in 3D"
      >
        Show in Clashes →
      </button>
    </div>
  );
};

const MembersList: React.FC<{ result: DevBuildResult }> = ({ result }) => (
  <div className="flex flex-col gap-1">
    {result.properties.members.map((m) => (
      <button
        key={m.name}
        type="button"
        className="flex flex-col gap-0.5 p-1 bg-gray-800/40 hover:bg-gray-700/50 rounded-sm text-left"
        onClick={() => selectPreviewObjects([m.name])}
        title="Select in the scene"
      >
        <span className="text-gray-200">
          {m.role ?? m.name} <span className="text-gray-500">· {m.type.toLowerCase()}</span>
        </span>
        {m.section && <Row label="section" value={m.section.name ?? m.section.type ?? "–"} />}
        {m.section && <Row label="h × w" value={`${mm(m.section.h)} × ${mm(m.section.w_top)}`} />}
        {typeof m.thickness === "number" && <Row label="thickness" value={mm(m.thickness)} />}
        {typeof m.length === "number" && <Row label="length" value={`${fmt(m.length, 3)} m`} />}
        <Row label="material" value={m.material?.name ?? "–"} />
        <Row label="cuts" value={String(m.booleans)} />
      </button>
    ))}
  </div>
);

const WeldsList: React.FC<{ result: DevBuildResult }> = ({ result }) => {
  const welds = result.properties.welds;
  if (welds.length === 0) return <div className="text-gray-400">The builder made no welds.</div>;
  return (
    <div className="flex flex-col gap-1">
      {welds.map((w) => (
        <button
          key={w.name}
          type="button"
          className="flex flex-col gap-0.5 p-1 bg-gray-800/40 hover:bg-gray-700/50 rounded-sm text-left"
          onClick={() => selectPreviewObjects([w.name])}
          title="Select in the scene"
        >
          <span className="text-gray-200 flex items-center gap-1.5">
            {w.symbol_svg && (
              // The core-generated symbol (ada.api.weld_symbols), inline so it takes the text colour.
              <span
                className="inline-flex shrink-0 text-gray-300"
                aria-hidden="true"
                dangerouslySetInnerHTML={{ __html: w.symbol_svg }}
              />
            )}
            {w.name}
            <span className="text-gray-500" title={w.weld_type ?? undefined}>
              · {weldTypeName(w.weld_type)}
            </span>
            {w.joins_its_members === false && (
              <span className="text-red-300" title={`touches: ${w.touches.join(", ") || "nothing"}`}>
                {" "}
                · does not touch {w.members.filter((m) => !w.touches.includes(m)).join(", ")}
              </span>
            )}
          </span>
          <Row label="throat" value={mm(w.throat)} />
          {(w.leg1 != null || w.leg2 != null) && <Row label="legs" value={`${mm(w.leg1)} / ${mm(w.leg2)}`} />}
          <Row label="joins" value={w.members.join(" + ") || "–"} />
          {w.intermittent && (
            <Row
              label="intermittent"
              value={`${mm(w.intermittent.length_on)} on / ${mm(w.intermittent.length_off)} off`}
            />
          )}
        </button>
      ))}
    </div>
  );
};

const Row: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="flex gap-2">
    <span className="text-gray-500 w-24 shrink-0">{label}</span>
    <span className="text-gray-200 min-w-0 truncate" title={value}>
      {value}
    </span>
  </div>
);
