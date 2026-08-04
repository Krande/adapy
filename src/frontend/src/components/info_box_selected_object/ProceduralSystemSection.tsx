import React, {useState} from 'react';
import {useCellBuilderStore, type BuilderSystem} from '@/state/cellBuilderStore';

// A routed run's mesh objects are named ``<System>_route_<n>`` (straight legs and
// fittings) or ``<System>_route`` (the pipe container). Strip that suffix to
// recover the owning system's name so a clicked cable-tray / duct / pipe segment
// can be linked back to the procedural system it belongs to.
function systemNameFromObject(objectName: string | null | undefined): string | null {
    if (!objectName) return null;
    const m = objectName.match(/^(.*)_route(?:_\d+)?$/);
    return m ? m[1] : null;
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

type Props = {
    // The name of the currently-clicked scene object (from the object-info store).
    objectName: string | null;
};

// Shows, for a clicked routed run, the procedural system it belongs to and the
// equipment/site terminals that system connects — the reverse link from result
// geometry back to the source topology model. Data comes from the cellbuilder
// store's loaded systems (populated whenever a procedural model is open), so it
// renders only when the clicked object is a route segment of a known system.
const ProceduralSystemSection: React.FC<Props> = ({objectName}) => {
    const [expanded, setExpanded] = useState(true);
    const systems = useCellBuilderStore((s) => s.systems);
    const cells = useCellBuilderStore((s) => s.cells);

    const sysName = systemNameFromObject(objectName);
    if (!sysName) return null;
    const system: BuilderSystem | undefined = Object.values(systems).find((sy) => sy.name === sysName);
    if (!system) return null;

    const equipTypeOf = (name: string): string | undefined =>
        Object.values(cells).find((c) => c.kind === 'equipment' && c.name === name)?.equipmentType;

    const equipmentConns = system.connections.filter((c) => c.equipment);
    const siteConns = system.connections.filter((c) => c.site);

    return (
        <div className="mt-2">
            <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="flex items-center gap-1 text-[12px] text-gray-100 hover:text-white"
                aria-expanded={expanded}
                aria-controls="procedural-system"
            >
                <Chevron open={expanded} />
                <span className="font-semibold">Procedural model</span>
            </button>
            {expanded && (
                <div id="procedural-system" className="mt-1 ml-4 table">
                    <Row label="System:">{system.name}</Row>
                    <Row label="Type:">
                        {system.type}
                        {system.medium ? ` · ${system.medium}` : ''}
                    </Row>
                    {equipmentConns.map((c, i) => (
                        <Row label={i === 0 ? 'Connects:' : ''} key={`eq-${c.equipment}-${c.port}-${i}`}>
                            <span className="text-gray-100">{c.equipment}</span>
                            {c.port ? <span className="text-gray-400"> · {c.port}</span> : null}
                            {equipTypeOf(c.equipment as string) ? (
                                <span className="text-gray-500"> ({equipTypeOf(c.equipment as string)})</span>
                            ) : null}
                        </Row>
                    ))}
                    {siteConns.map((c, i) => (
                        <Row
                            label={i === 0 && equipmentConns.length === 0 ? 'Connects:' : ''}
                            key={`site-${c.site}-${i}`}
                        >
                            <span className="text-gray-100">{c.site}</span>
                            <span className="text-gray-400"> · site {c.direction ?? 'IN'}</span>
                        </Row>
                    ))}
                </div>
            )}
        </div>
    );
};

export default ProceduralSystemSection;
