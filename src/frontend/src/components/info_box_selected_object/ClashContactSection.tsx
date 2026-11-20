// The Selected Object panel's Contact section: the clash contact (a joint of the Clashes tab's
// result) last pointed at in the 3D view -- its marker or its contact patch clicked
// (`clashContactPickStore`). What the result holds of it: its joint type and the pass that found it,
// the members in contact (kind, section, member type, role and the end at the contact), what the
// checker measured (penetration / overlap, margin / gap, contact area, angle, normal), where it is,
// and the specs that claim it, the one detailing would use marked. A link opens it in Connections >
// Review, its detail open. Nothing while no contact is picked, or once the result no longer has it.

import React from "react";

import { openConnectionsTab } from "@/layout/connectionsTabActions";
import { rankSpecs, specProviderLabel, specProviderOf } from "@/services/clashSpecProviders";
import { effectiveSpecProviders, groupColor, specForJoint, useClashCheckStore } from "@/state/clashCheckStore";
import { useClashContactPickStore } from "@/state/clashContactPickStore";
import { JOINTS_REVIEW_TAB } from "@/state/connectionBuilderTabStore";
import { ContactFacts } from "@/components/info_box_scene/joints/JointDetail";

const fmt = (n: number) => (Number.isFinite(n) ? n.toFixed(2) : "—");

/** Open the contact `jointId` in Connections > Review, its detail open. */
export function showContactInConnections(jointId: string): void {
  useClashCheckStore.getState().focusJoint(jointId);
  useClashContactPickStore.getState().pick(jointId);
  openConnectionsTab(JOINTS_REVIEW_TAB);
}

const ClashContactSection: React.FC = () => {
  const jointId = useClashContactPickStore((s) => s.jointId);
  const result = useClashCheckStore((s) => s.result);
  const adminSpecProviders = useClashCheckStore((s) => s.adminSpecProviders);
  const specProvidersOverride = useClashCheckStore((s) => s.specProvidersOverride);
  const joint = jointId ? result?.jointsById.get(jointId) ?? null : null;
  if (!joint || !result) return null;
  const providers = effectiveSpecProviders({ adminSpecProviders, specProvidersOverride });
  const best = specForJoint(joint, providers);
  const offered = new Set(rankSpecs(joint.applicable, providers).map((s) => s.spec));
  return (
    <section className="mt-2 flex flex-col gap-1.5 text-[11px]" aria-label="Clash contact" data-clash-contact={joint.id}>
      <div className="flex items-center gap-2 text-[12px] text-gray-100">
        <span
          className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: groupColor(result, joint.typeKey) }}
          title="Its group's colour in the 3D view"
        />
        <span className="font-semibold">Contact</span>
        <span className="truncate text-gray-300" title={joint.typeKey}>
          {joint.typeLabel}
        </span>
        <span className="ml-auto font-mono text-gray-500" title={joint.id}>
          {joint.id.slice(0, 8)}
        </span>
      </div>
      <div className="ml-4 flex flex-col gap-1.5">
        <div className="text-gray-400">
          found by <span className="text-gray-300">{joint.origin}</span> · at{" "}
          <span className="tabular-nums text-gray-300">
            {fmt(joint.centre[0])}, {fmt(joint.centre[1])}, {fmt(joint.centre[2])}
          </span>
        </div>
        <div className="flex flex-col gap-0.5">
          <div className="text-gray-500">members in contact</div>
          {joint.members.map((m) => (
            <div key={`${m.name}-${m.guid ?? ""}`} className="flex items-center gap-2 text-gray-300">
              <span className="truncate flex-1 min-w-0" title={m.guid ?? m.name}>
                {m.name}
              </span>
              <span className="text-gray-500 shrink-0">{m.kind}</span>
              {m.section && <span className="text-gray-500 shrink-0">{m.section}</span>}
              {m.memberType && <span className="text-gray-500 shrink-0">{m.memberType}</span>}
              {m.role && (
                <span className="text-sky-300 shrink-0" title={m.end ? `its ${m.end} is at the contact` : undefined}>
                  {m.role}
                  {m.end ? ` (${m.end})` : ""}
                </span>
              )}
            </div>
          ))}
        </div>
        {joint.contact && <ContactFacts contact={joint.contact} />}
        <div className="flex flex-col gap-0.5">
          <div className="text-gray-500">claimed by</div>
          {joint.applicable.length === 0 ? (
            <div className="italic text-gray-500">No registered spec matches this joint.</div>
          ) : (
            joint.applicable.map((spec) => (
              <div
                key={spec.spec}
                className={`flex items-center gap-2 ${offered.has(spec.spec) ? "text-gray-300" : "text-gray-600 line-through"}`}
                title={offered.has(spec.spec) ? undefined : "Its provider is not enabled for detailing here"}
              >
                <span className="truncate flex-1 min-w-0">
                  {spec.spec}
                  {best?.spec === spec.spec ? " ✓" : ""}
                </span>
                <span className="text-gray-500 shrink-0">{specProviderLabel(specProviderOf(spec))}</span>
              </div>
            ))
          )}
        </div>
        <button
          type="button"
          className="self-start text-blue-300 hover:text-blue-200 hover:underline"
          title="Open this contact in Connections > Review, its detail open"
          onClick={() => showContactInConnections(joint.id)}
        >
          Open in Connections ▸
        </button>
      </div>
    </section>
  );
};

export default ClashContactSection;
