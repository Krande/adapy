import React, {useState} from 'react';
import {selectInOtherModel} from '@/utils/scene/crossModelSelect';
import type {LinkResult} from '@/state/lineageStore';
import {useViewerStores} from '@/state/AdaViewerContext';

// Decimal places for the "Clicked at" coordinates, matching the
// precision the old standalone block used before the fold-in.
const COORD_PREC = 3;

const Chevron: React.FC<{open: boolean}> = ({open}) => (
    <svg
        viewBox="0 0 16 16"
        className={
            "w-3 h-3 transition-transform duration-150 ease-out " +
            (open ? "rotate-90" : "rotate-0")
        }
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
    >
        <path d="M6 4l4 4-4 4"/>
    </svg>
);

// Backend ships SI everywhere (metres for length, Pa for stress, kg/m³
// for density). The viewer's convention is mm for cross-section and
// thickness, MPa for yield, GPa for Young's modulus. Conversion happens
// at render time so the wire format stays one canonical unit system.
const M_TO_MM = 1000;
const PA_TO_MPA = 1e-6;
const PA_TO_GPA = 1e-9;

type MaterialDict = {
    name?: string | null;
    E?: number | null;
    rho?: number | null;
    sig_y?: number | null;
    sig_u?: number | null;
    v?: number | null;
};

type SectionDict = {
    name?: string | null;
    type?: string | null;
    h?: number | null;
    w_top?: number | null;
    w_btn?: number | null;
    t_w?: number | null;
    t_ftop?: number | null;
    t_fbtn?: number | null;
    r?: number | null;
    wt?: number | null;
};

type BeamMeta = {
    type: 'Beam';
    name?: string;
    section?: SectionDict | null;
    material?: MaterialDict | null;
};

type PlateMeta = {
    type: 'Plate';
    name?: string;
    thickness?: number | null;
    material?: MaterialDict | null;
};

type Props = {
    data: any | null;
};

const fmtMm = (v: number | null | undefined): string => {
    if (v == null) return '—';
    return `${(v * M_TO_MM).toFixed(1)} mm`;
};

const fmtGpa = (v: number | null | undefined): string => {
    if (v == null) return '—';
    return `${(v * PA_TO_GPA).toFixed(0)} GPa`;
};

const fmtMpa = (v: number | null | undefined): string => {
    if (v == null) return '—';
    return `${(v * PA_TO_MPA).toFixed(0)} MPa`;
};

const fmtDensity = (v: number | null | undefined): string => {
    if (v == null) return '—';
    return `${v.toFixed(0)} kg/m³`;
};

const fmtNu = (v: number | null | undefined): string => {
    if (v == null) return '—';
    return v.toFixed(2);
};

const Row: React.FC<{label: string; children: React.ReactNode}> = ({label, children}) => (
    <div className="table-row">
        <div className="table-cell w-24 align-top text-gray-200">{label}</div>
        <div className="table-cell w-48 break-all">{children}</div>
    </div>
);

const SectionRows: React.FC<{section: SectionDict | null | undefined}> = ({section}) => {
    if (!section) return <Row label="Section:">—</Row>;
    return (
        <>
            <Row label="Section:">{section.name ?? '—'}</Row>
            {section.type && <Row label="Profile:">{section.type}</Row>}
            {section.h != null && <Row label="h:">{fmtMm(section.h)}</Row>}
            {(section.w_top != null || section.w_btn != null) && (
                <Row label="b:">
                    {section.w_top != null ? fmtMm(section.w_top) : '—'}
                    {section.w_btn != null && section.w_btn !== section.w_top
                        ? ` / ${fmtMm(section.w_btn)}`
                        : ''}
                </Row>
            )}
            {section.t_w != null && <Row label="t_w:">{fmtMm(section.t_w)}</Row>}
            {(section.t_ftop != null || section.t_fbtn != null) && (
                <Row label="t_f:">
                    {section.t_ftop != null ? fmtMm(section.t_ftop) : '—'}
                    {section.t_fbtn != null && section.t_fbtn !== section.t_ftop
                        ? ` / ${fmtMm(section.t_fbtn)}`
                        : ''}
                </Row>
            )}
            {section.r != null && <Row label="r:">{fmtMm(section.r)}</Row>}
            {section.wt != null && <Row label="wt:">{fmtMm(section.wt)}</Row>}
        </>
    );
};

const MaterialRows: React.FC<{material: MaterialDict | null | undefined}> = ({material}) => {
    if (!material) return <Row label="Material:">—</Row>;
    return (
        <>
            <Row label="Material:">{material.name ?? '—'}</Row>
            {material.E != null && <Row label="E:">{fmtGpa(material.E)}</Row>}
            {material.rho != null && <Row label="ρ:">{fmtDensity(material.rho)}</Row>}
            {material.sig_y != null && <Row label="σ_y:">{fmtMpa(material.sig_y)}</Row>}
            {material.sig_u != null && <Row label="σ_u:">{fmtMpa(material.sig_u)}</Row>}
            {material.v != null && <Row label="ν:">{fmtNu(material.v)}</Row>}
        </>
    );
};

const LinkRow: React.FC<{link: NonNullable<LinkResult>}> = ({link}) => {
    if (link.kind === 'fea') {
        const {file, name} = link.cad;
        return (
            <Row label="Linked CAD:">
                <span>{name}</span>
                <button
                    type="button"
                    onClick={() => void selectInOtherModel({file, nodeNames: [name]})}
                    className="ml-2 bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white text-[11px] rounded px-2 py-0.5"
                    title={`Switch to ${file} and select ${name}`}
                >
                    Select in CAD
                </button>
            </Row>
        );
    }
    // CAD → FEA. Multiple FEA files can derive from the same CAD; we
    // group rows per file so the user can pick which analysis to jump
    // to. Element count is the per-file total for that beam/plate.
    return (
        <>
            {link.fem.map((entry) => (
                <Row label="Meshed as:" key={entry.file}>
                    <span>
                        {entry.elementNames.length} element{entry.elementNames.length === 1 ? '' : 's'}{' '}
                        in {entry.file}
                    </span>
                    <button
                        type="button"
                        onClick={() => void selectInOtherModel({file: entry.file, nodeNames: entry.elementNames})}
                        className="ml-2 bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white text-[11px] rounded px-2 py-0.5"
                        title={`Switch to ${entry.file} and select all ${entry.elementNames.length} elements`}
                    >
                        Select FEA elements
                    </button>
                </Row>
            ))}
        </>
    );
};

const ClickedAtRow: React.FC = () => {
    const {useObjectInfoStore, useModelState} = useViewerStores();
    const clickCoord = useObjectInfoStore((s) => s.clickCoordinate);
    const zIsUp = useModelState((s) => s.zIsUp);
    if (!clickCoord) return null;
    const x = clickCoord.x.toFixed(COORD_PREC);
    const y = clickCoord.y.toFixed(COORD_PREC);
    const z = clickCoord.z.toFixed(COORD_PREC);
    // Mirror the original axis-swap convention from CoordinateDisplay:
    // viewer scene is y-up by default, but adapy's Z-up world is what
    // users think in — so when zIsUp is set, show the world tuple
    // (x, y, z) directly; otherwise re-order to compensate.
    const display = zIsUp ? `(${x}, ${y}, ${z})` : `(${x}, ${z}, ${y})`;
    return <Row label="Clicked at:">{display}</Row>;
};

const ObjectMetadataPanel: React.FC<Props> = ({data}) => {
    const {useObjectInfoStore, useLineageStore} = useViewerStores();
    // Default collapsed: most clicks are just for selection / hide /
    // jump, not for inspecting properties. Folding keeps the info box
    // compact and the chevron tells the user where the data lives.
    const [expanded, setExpanded] = useState(false);
    // The link is derived live from the lineage store and the current
    // selection (file + name) — keeps it reactive to file load/unload
    // without going back through the server.
    const fileName = useObjectInfoStore((s) => s.fileName);
    const clickedName = useObjectInfoStore((s) => s.name);
    const link = useLineageStore((s) => s.findLink(fileName, clickedName));
    // Prefer metadata embedded in the GLB extension (when the export
    // used ``embed_object_metadata=True``) over what the server
    // returned in MESH_INFO_REPLY. The embedded path works for GLB-
    // only uploads where the server has no source IFC to walk; the
    // server-fetched path stays as a fallback for IFC uploads.
    const embeddedMeta = useLineageStore((s) => s.getMetadata(fileName, clickedName));
    const effectiveData = embeddedMeta ?? data;
    // Panel always renders when there's a selection — even without
    // any structured metadata — because it still hosts the clicked-
    // coordinate row and the cross-model link buttons.
    const meta = (effectiveData ?? null) as BeamMeta | PlateMeta | null;
    const known = meta?.type === 'Beam' || meta?.type === 'Plate';
    return (
        <div className="mt-2">
            <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="flex items-center gap-1 text-[12px] text-gray-100 hover:text-white"
                aria-expanded={expanded}
                aria-controls="object-properties"
            >
                <Chevron open={expanded} />
                <span className="font-semibold">Properties</span>
            </button>
            {expanded && (
                <div id="object-properties" className="mt-1 ml-4 table">
                    {meta && !known && <Row label="Type:">{(meta as any).type ?? 'Unknown'}</Row>}
                    {meta && !known && <Row label="Info:">No metadata available</Row>}
                    {meta?.type === 'Beam' && (
                        <>
                            <Row label="Type:">Beam</Row>
                            <SectionRows section={(meta as BeamMeta).section} />
                            <MaterialRows material={meta.material} />
                        </>
                    )}
                    {meta?.type === 'Plate' && (
                        <>
                            <Row label="Type:">Plate</Row>
                            <Row label="Thickness:">{fmtMm((meta as PlateMeta).thickness)}</Row>
                            <MaterialRows material={meta.material} />
                        </>
                    )}
                    <ClickedAtRow />
                    {link && <LinkRow link={link} />}
                </div>
            )}
        </div>
    );
};

export default ObjectMetadataPanel;
