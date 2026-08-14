import React from "react";

import {
  followerParams,
  subscribeProcedural,
  type PreviewReadyMsg,
} from "@/utils/cellbuilder/proceduralChannel";

// The follower half of the cross-tab side-by-side workflow. A tab opened with
// `?pfollow=<modelId>&pscope=<scope>` becomes a live result viewer: it subscribes
// to the procedural BroadcastChannel and, whenever the editing tab reports a
// server build for that model, loads the compiled GLB (replacing the previous
// one). No editing UI — just the result, updating as you edit next door.
//
// Mounted always; it self-disables (renders nothing, subscribes to nothing) when
// the URL carries no follower params, so a normal viewer tab is unaffected.
const ProceduralFollowerController: React.FC = () => {
  const follow = React.useMemo(() => followerParams(), []);
  const [status, setStatus] = React.useState<{
    name: string;
    lod: "sim" | "detail";
    count: number;
  } | null>(null);

  React.useEffect(() => {
    if (!follow) return;
    let count = 0;
    const onMsg = (msg: PreviewReadyMsg) => {
      if (msg.modelId !== follow.modelId) return; // another model's build
      count += 1;
      setStatus({ name: msg.name || "model", lod: msg.lod, count });
      void import("@/utils/scene/handlers/view_file_object_from_server").then(
        ({ load_glb_by_url_rest }) => {
          // Distinct source name per lod so sim/detail don't collide; replaces
          // the previous result so the follower always shows the latest build.
          const src =
            msg.lod === "detail"
              ? "procedural-follow-detail"
              : "procedural-follow";
          void load_glb_by_url_rest(follow.scope, msg.derivedKey, src);
        },
      );
    };
    return subscribeProcedural(onMsg);
  }, [follow]);

  if (!follow) return null;

  return (
    <div className="absolute left-1/2 -translate-x-1/2 top-3 z-20 pointer-events-none">
      <div className="bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] text-[var(--ada-panel-text)] shadow-lg rounded-full px-3 py-1 text-xs flex items-center gap-2">
        <span className="inline-block w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
        {status ? (
          <span>
            Following <span className="font-semibold">{status.name}</span> ·{" "}
            {status.lod === "detail" ? "detail" : "simulation"} · update{" "}
            {status.count}
          </span>
        ) : (
          <span className="text-gray-400">
            Following live results — waiting for the first build…
          </span>
        )}
      </div>
    </div>
  );
};

export default ProceduralFollowerController;
