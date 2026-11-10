// ConnectionBuilder — the studio side of `ada dev connections`: pick a spec from
// the served connection library, edit each member (a beam's section, a plate's
// thickness and extent, the angle between them) and see the joint rebuilt, on
// every edit here and on every saved change to the library's files.
//
// State lives in connectionBuilderStore; the live loop (event stream, debounced
// builds, scene loading) in services/devConnections/connectionBuilderSession.

import React, { useEffect, useMemo, useState } from "react";

import type { ComponentSpecRoleSchema } from "@/services/api/components";
import type { DevConnectionSpec } from "@/services/api/devConnections";
import {
  reloadLibrary,
  requestBuild,
  restartLibrary,
  startConnectionBuilderSession,
} from "@/services/devConnections/connectionBuilderSession";
import {
  type ConnectionBuildState,
  effectiveInputs,
  useConnectionBuilderStore,
} from "@/state/connectionBuilderStore";
import { PanelHeaderActions, usePanelHeaderActions } from "@/components/ui/PanelFrame";

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
    <>
      <LibraryHeader />
      {library.status === "error" && library.error && (
        <ErrorBox title="The library did not load" error={library.error.error} traceback={library.error.traceback} />
      )}

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
    </>
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
    </div>
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

const BuildStatus: React.FC<{ build: ConnectionBuildState; specName: string }> = ({ build, specName }) => {
  if (build.status === "idle") return null;
  if (build.status === "building") {
    return <div className="text-gray-300">Building {build.specName === specName ? "" : build.specName}…</div>;
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

const ErrorBox: React.FC<{ title: string; error: string; traceback: string | null }> = ({ title, error, traceback }) => {
  const [open, setOpen] = useState(true);
  return (
    <div className="flex flex-col gap-1 p-1 border border-red-800 bg-red-950/50 rounded-sm">
      <div className="flex items-center gap-2">
        <span className="text-red-300 font-medium flex-1 min-w-0 break-words">
          {title}: {error}
        </span>
        {traceback && (
          <button type="button" className="text-gray-400 hover:text-white shrink-0" onClick={() => setOpen(!open)}>
            {open ? "hide" : "traceback"}
          </button>
        )}
      </div>
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

/** The classic frame: a dark card in the menu column, shown while the panel is toggled on. */
const ConnectionBuilder: React.FC = () => {
  const isVisible = useConnectionBuilderStore((s) => s.isVisible);
  const hosted = usePanelHeaderActions().length > 0;
  if (!isVisible) return null;
  return (
    <div className="flex flex-col gap-2 text-xs text-white p-2 bg-gray-900/70 rounded-md w-[340px] max-h-[80vh] overflow-auto">
      {hosted && (
        <div className="flex items-center gap-2">
          <span className="font-bold">Connection builder</span>
          <PanelHeaderActions />
        </div>
      )}
      <ConnectionBuilderBody />
    </div>
  );
};

export default ConnectionBuilder;
