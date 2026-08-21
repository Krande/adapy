import React from "react";

import { useCellBuilderStore } from "@/state/cellBuilderStore";
import type {
  DetailingFieldSpec,
  DetailingJointTypeSpec,
} from "@/services/viewerApi";
import { clampField } from "@/utils/cellbuilder/detailingOptions";

/**
 * The "Detailing" tab — an ENGINE-AGNOSTIC, fully data-driven form.
 *
 * Every control here is generated from the SELECTED detailing engine's
 * advertised `joint_types` specs (fetched onto `detailingEngines`): a toggle per
 * joint type plus one input per advertised field (`number` / `bool` / `enum`).
 * Nothing joint-specific is hardcoded — a new engine advertising different joint
 * types / fields just renders, exactly like the `+ Cell` / `+ Opening` pickers
 * are populated from their advertised type specs. Shown only when a detailing
 * engine is selected (`selectedDetailing !== "none"`); the Compile-settings
 * "Detailing" select owns the engine choice.
 *
 * Mobile: a single scrolling column (each joint type is one block), matching the
 * other cellbuilder tabs — the parent panel body handles the scroll.
 *
 * Selector discipline: every `useCellBuilderStore` call returns a STORED value
 * (primitive, or the store's own array/object/function reference) — never a
 * freshly-built array/object — so the panel can't trip the unstable-selector
 * infinite-render crash class.
 */

const inputCls =
  "text-content bg-surface-2 border border-edge rounded-sm px-1 py-0.5 w-20";
const btn =
  "px-2 py-1 rounded-sm bg-accent text-white disabled:opacity-50 pointer-fine:hover:bg-accent";

const fieldLabel = (f: DetailingFieldSpec): string =>
  (f.label ?? f.name) + (f.unit ? ` (${f.unit})` : "");

const NumberField: React.FC<{
  jointSlug: string;
  field: DetailingFieldSpec;
  value: number;
}> = ({ jointSlug, field, value }) => {
  const setField = useCellBuilderStore((s) => s.setDetailingField);
  return (
    <label className="flex items-center gap-1.5 text-content">
      <span className="min-w-[9rem]">{fieldLabel(field)}</span>
      <input
        type="number"
        className={inputCls}
        value={Number.isFinite(value) ? value : ""}
        min={field.min}
        max={field.max}
        step="any"
        aria-label={fieldLabel(field)}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (!Number.isFinite(n)) return;
          setField(jointSlug, field.name, clampField(field, n));
        }}
      />
    </label>
  );
};

const BoolField: React.FC<{
  jointSlug: string;
  field: DetailingFieldSpec;
  value: boolean;
}> = ({ jointSlug, field, value }) => {
  const setField = useCellBuilderStore((s) => s.setDetailingField);
  return (
    <label className="flex items-center gap-1.5 text-content cursor-pointer">
      <input
        type="checkbox"
        className="accent-blue-600"
        checked={value}
        aria-label={fieldLabel(field)}
        onChange={(e) => setField(jointSlug, field.name, e.target.checked)}
      />
      <span>{fieldLabel(field)}</span>
    </label>
  );
};

const EnumField: React.FC<{
  jointSlug: string;
  field: DetailingFieldSpec;
  value: string;
}> = ({ jointSlug, field, value }) => {
  const setField = useCellBuilderStore((s) => s.setDetailingField);
  return (
    <label className="flex items-center gap-1.5 text-content">
      <span className="min-w-[9rem]">{fieldLabel(field)}</span>
      <select
        className={inputCls + " w-auto"}
        value={value}
        aria-label={fieldLabel(field)}
        onChange={(e) => setField(jointSlug, field.name, e.target.value)}
      >
        {(field.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
};

const JointField: React.FC<{
  jointSlug: string;
  field: DetailingFieldSpec;
  value: number | boolean | string | undefined;
}> = ({ jointSlug, field, value }) => {
  if (field.type === "bool")
    return (
      <BoolField
        jointSlug={jointSlug}
        field={field}
        value={Boolean(value)}
      />
    );
  if (field.type === "number")
    return (
      <NumberField
        jointSlug={jointSlug}
        field={field}
        value={typeof value === "number" ? value : Number(value)}
      />
    );
  return (
    <EnumField
      jointSlug={jointSlug}
      field={field}
      value={value != null ? String(value) : ""}
    />
  );
};

const JointTypeBlock: React.FC<{ spec: DetailingJointTypeSpec }> = ({
  spec,
}) => {
  const option = useCellBuilderStore((s) => s.detailingOptions[spec.slug]);
  const setEnabled = useCellBuilderStore((s) => s.setDetailingJointEnabled);
  const counts = useCellBuilderStore((s) => s.detailingJointCounts);
  const enabled = option?.enabled ?? false;
  const detected = counts?.[spec.slug];

  return (
    <div className="border border-edge rounded-md bg-black/10 p-2 flex flex-col gap-2">
      <label
        className="flex items-center gap-1.5 cursor-pointer font-semibold"
        title={spec.description}
      >
        <input
          type="checkbox"
          className="accent-blue-600"
          checked={enabled}
          aria-label={spec.name}
          onChange={(e) => setEnabled(spec.slug, e.target.checked)}
        />
        <span>{spec.name}</span>
        {detected != null && (
          <span className="text-content-muted text-[10px] ml-auto">
            {detected} detected
          </span>
        )}
      </label>
      {enabled && (spec.fields?.length ?? 0) > 0 && (
        <div className="flex flex-col gap-1 pl-5">
          {spec.fields!.map((f) => (
            <JointField
              key={f.name}
              jointSlug={spec.slug}
              field={f}
              value={option?.fields?.[f.name]}
            />
          ))}
        </div>
      )}
    </div>
  );
};

const DetailingPanel: React.FC = () => {
  const selectedDetailing = useCellBuilderStore((s) => s.selectedDetailing);
  const engines = useCellBuilderStore((s) => s.detailingEngines);
  const counts = useCellBuilderStore((s) => s.detailingJointCounts);
  const compile = useCellBuilderStore((s) => s.compile);
  const compileJob = useCellBuilderStore((s) => s.compileJob);

  const engine = engines.find((e) => e.slug === selectedDetailing);
  const jointTypes = engine?.joint_types ?? [];
  const busy = compileJob != null;

  // Total detected across joint types, when a compile reported counts.
  const totalDetected =
    counts != null
      ? Object.values(counts).reduce((a, b) => a + b, 0)
      : null;

  if (selectedDetailing === "none") {
    return (
      <div className="text-content-muted">
        No detailing engine selected. Pick one under Compile settings ▸ Detailing
        to add fabrication connection joints.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-0.5">
        <span className="font-semibold">{engine?.name ?? selectedDetailing}</span>
        {engine?.description && (
          <span className="text-content-muted text-[11px]">
            {engine.description}
          </span>
        )}
        {engine && !engine.inprocess && (
          <span className="text-warn text-[11px]">
            External engine — runs on its own worker pool.
          </span>
        )}
      </div>

      {jointTypes.length === 0 ? (
        <div className="text-content-muted">
          This engine advertises no configurable joint types.
        </div>
      ) : (
        <>
          <span className="text-content-muted text-[11px]">
            Joint types advertised by the engine:
          </span>
          {jointTypes.map((jt) => (
            <JointTypeBlock key={jt.slug} spec={jt} />
          ))}
        </>
      )}

      {totalDetected != null && (
        <div className="text-content text-[11px]">
          Detected joints: {totalDetected}
        </div>
      )}

      <button
        className={btn + " self-start mt-1"}
        disabled={busy}
        onClick={() => void compile(true)}
        title="Recompile the committed model applying the selected detailing engine and the options above"
      >
        {busy ? "Compiling…" : "Recompile with detailing"}
      </button>
    </div>
  );
};

export default DetailingPanel;
