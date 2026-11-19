// Connections > Review, "Detail models": the scope's kept detail models, newest first -- every
// detail run of a clash check or a clash run is one (`detailModelsStore`). Each can be loaded into
// the scene, which also lists its joints under the produced ones, or deleted.

import React from "react";

import { useModelState } from "@/state/modelState";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { detailModelLine, useDetailModelsStore } from "@/state/detailModelsStore";
import type { WireDetailModel } from "@/services/api/clashCheck";

const btn =
  "rounded-sm px-1.5 py-0.5 text-[11px] text-gray-100 disabled:opacity-50 bg-gray-700 hover:bg-gray-600 shrink-0";

const DetailModelRow: React.FC<{ scope: string; model: WireDetailModel }> = ({ scope, model }) => {
  const loaded = useModelState((s) => s.loadedSourceNames.has(model.glb_key));
  const busyId = useDetailModelsStore((s) => s.busyId);
  const open = useDetailModelsStore((s) => s.open);
  const remove = useDetailModelsStore((s) => s.remove);
  const done = model.status === "done";
  const builtBy = model.built_by ? Object.entries(model.built_by).map(([spec, n]) => `${spec}: ${n}`) : [];
  const title = [
    model.label,
    model.capability ? `pool: ${model.capability}` : null,
    builtBy.length ? `built by ${builtBy.join(", ")}` : null,
    model.error ? `error: ${model.error}` : null,
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <li className="flex items-start gap-1 px-2 py-1" data-detail-model={model.detail_id}>
      <div className="flex-1 min-w-0" title={title}>
        <div className={"truncate " + (loaded ? "text-gray-100" : "text-gray-300")}>{model.label}</div>
        <div className={"truncate " + (model.status === "error" ? "text-red-300/80" : "text-gray-500")}>
          {detailModelLine(model)}
        </div>
      </div>
      <button
        type="button"
        className={btn}
        disabled={!done || loaded || busyId !== null}
        title={loaded ? "In the scene" : done ? "Load this model into the scene and list its joints above" : "Not built"}
        onClick={() => void open(scope, model)}
      >
        {busyId === model.detail_id ? "…" : loaded ? "loaded" : "Load"}
      </button>
      <button
        type="button"
        className={btn}
        disabled={busyId !== null}
        title="Delete this detail model"
        onClick={() => {
          if (!window.confirm(`Delete the detail model "${model.label}" (${model.created_at})? This cannot be undone.`)) return;
          void remove(scope, model.detail_id);
        }}
      >
        Delete
      </button>
    </li>
  );
};

const DetailModels: React.FC = () => {
  const scope = scopeUrlPart(useScopeStore((s) => s.current));
  const models = useDetailModelsStore((s) => s.models);
  const listScope = useDetailModelsStore((s) => s.scope);
  const error = useDetailModelsStore((s) => s.error);
  if (listScope !== scope || models.length === 0) return null;
  return (
    <section className="flex flex-col gap-1 text-[11px]" aria-label="Detail models">
      <div className="text-sm text-gray-300">
        <span className="font-semibold">{models.length}</span> detail model{models.length === 1 ? "" : "s"}{" "}
        <span className="text-gray-500">— generated from clashes, newest first</span>
      </div>
      {error && <div className="text-red-300/80">{error}</div>}
      <ul className="max-h-56 overflow-y-auto divide-y divide-gray-800 rounded-sm border border-gray-800">
        {models.map((m) => (
          <DetailModelRow key={m.detail_id} scope={scope} model={m} />
        ))}
      </ul>
    </section>
  );
};

export default DetailModels;
