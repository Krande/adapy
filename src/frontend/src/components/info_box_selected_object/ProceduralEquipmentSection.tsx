import React, {useState} from 'react';
import {useCellBuilderStore, type BuilderCell, type BuilderSystem} from '@/state/cellBuilderStore';

// A placed equipment's body mesh is named ``<Equipment>_body`` (see
// ada.topo_model.equipment._add_body); catalog/CAD-backed equipment may carry
// the bare equipment name. Recover the owning equipment name so a clicked body
// in the compiled result links back to the procedural equipment it came from.
function equipmentNameFromObject(
    objectName: string | null | undefined,
    cells: Record<string, BuilderCell>,
): BuilderCell | null {
    if (!objectName) return null;
    const equipment = Object.values(cells).filter((c) => c.kind === 'equipment');
    // Exact match first (bare-name bodies), then the ``_body`` suffix, then the
    // longest equipment name that prefixes a compound shape name (``<eq>_<part>``)
    // so a multi-shape catalog unit still resolves to its equipment.
    const exact = equipment.find((c) => c.name === objectName);
    if (exact) return exact;
    const stripped = objectName.replace(/_body$/, '');
    const byBody = equipment.find((c) => c.name === stripped);
    if (byBody) return byBody;
    const prefixed = equipment
        .filter((c) => objectName.startsWith(c.name + '_'))
        .sort((a, b) => b.name.length - a.name.length);
    return prefixed[0] ?? null;
}

const Chevron: React.FC<{open: boolean}> = ({open}) => (
    <svg
        viewBox="0 0 16 16"
        className={
            'w-3 h-3 transition-transform duration-150 ease-out ' +
            (open ? 'rotate-90' : 'rotate-0')
        }
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
    >
        <path d="M6 4l4 4-4 4" />
    </svg>
);

const Row: React.FC<{label: string; children: React.ReactNode}> = ({label, children}) => (
    <div className="table-row">
        <div className="table-cell w-24 align-top text-gray-200">{label}</div>
        <div className="table-cell w-48 break-all">{children}</div>
    </div>
);

const num = (v: unknown): number | null => {
    const n = typeof v === 'number' ? v : Number(v);
    return Number.isFinite(n) ? n : null;
};
const fmt = (v: number) => (Math.round(v * 1000) / 1000).toString();
const fmtVec = (v: [number, number, number]) => `(${v.map(fmt).join(', ')})`;

type Props = {
    // The name of the currently-clicked scene object (from the object-info store).
    objectName: string | null;
};

// Shows, for a clicked equipment body in the compiled result, the procedural
// equipment it came from — its type, placement, mass and the systems wired to
// it — the reverse link from result geometry back to the source topology model,
// the equipment analogue of ProceduralSystemSection (which does this for routed
// runs). Data comes from the cellbuilder store (populated whenever a procedural
// model is open), so it renders only when the clicked object is a known
// equipment unit.
const ProceduralEquipmentSection: React.FC<Props> = ({objectName}) => {
    // All hooks up-front — no store hooks below the early returns, or a render
    // that bails early (clicking non-equipment geometry) would run fewer hooks
    // than one that didn't and crash with React #310 (hooks-count mismatch).
    const [expanded, setExpanded] = useState(true);
    const cells = useCellBuilderStore((s) => s.cells);
    const systems = useCellBuilderStore((s) => s.systems);
    const focusSystem = useCellBuilderStore((s) => s.focusSystem);
    const focusEquipment = useCellBuilderStore((s) => s.focusEquipment);

    const eq = equipmentNameFromObject(objectName, cells);
    if (!eq) return null;

    const rot = eq.rotation && eq.rotation.some((r) => Math.abs(r) > 1e-9) ? eq.rotation : null;
    const massDry = num(eq.params?.massDry);
    const massCont = num(eq.params?.massCont);
    const space = (eq.params?.SPACE_NAME as string | undefined) ?? undefined;
    const description = (eq.params?.DESCRIPTION as string | undefined) ?? undefined;
    // Systems with at least one connection to this equipment, deduped, with the
    // port(s) they land on.
    const connected: {system: BuilderSystem; ports: string[]}[] = Object.values(systems)
        .map((sy) => ({
            system: sy,
            ports: sy.connections.filter((c) => c.equipment === eq.name).map((c) => c.port ?? ''),
        }))
        .filter((e) => e.ports.length > 0);

    const linkCls = 'text-blue-300 hover:text-blue-200 hover:underline cursor-pointer';

    return (
        <div className="mt-2">
            <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="flex items-center gap-1 text-[12px] text-gray-100 hover:text-white"
                aria-expanded={expanded}
                aria-controls="procedural-equipment"
            >
                <Chevron open={expanded} />
                <span className="font-semibold">Procedural equipment</span>
            </button>
            {expanded && (
                <div id="procedural-equipment" className="mt-1 ml-4 table">
                    <Row label="Equipment:">
                        <button
                            type="button"
                            className={linkCls}
                            onClick={() => focusEquipment(eq.name)}
                            title="Select this equipment in the cellbuilder"
                        >
                            {eq.name}
                        </button>
                    </Row>
                    <Row label="Type:">
                        {eq.equipmentType ?? description ?? '—'}
                        {description && description !== eq.equipmentType ? (
                            <span className="text-gray-500"> ({description})</span>
                        ) : null}
                    </Row>
                    {space ? <Row label="Space:">{space}</Row> : null}
                    <Row label="Position:">{fmtVec(eq.origin)}</Row>
                    <Row label="Size:">{fmtVec(eq.size)}</Row>
                    {rot ? <Row label="Rotation:">{fmtVec(rot)}°</Row> : null}
                    {massDry !== null || massCont !== null ? (
                        <Row label="Mass:">
                            {massDry !== null ? `${fmt(massDry)} (dry)` : ''}
                            {massCont ? ` · ${fmt(massCont)} (cont.)` : ''}
                        </Row>
                    ) : null}
                    {connected.map((e, i) => (
                        <Row label={i === 0 ? 'Systems:' : ''} key={`sys-${e.system.id}`}>
                            <button
                                type="button"
                                className={linkCls}
                                onClick={() => focusSystem(e.system.name)}
                                title="Open this system in the Systems inspector"
                            >
                                {e.system.name}
                            </button>
                            <span className="text-gray-400"> · {e.system.type}</span>
                            {e.ports.filter(Boolean).length ? (
                                <span className="text-gray-500"> ({e.ports.filter(Boolean).join(', ')})</span>
                            ) : null}
                        </Row>
                    ))}
                </div>
            )}
        </div>
    );
};

export default ProceduralEquipmentSection;
