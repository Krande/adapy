import React, {useMemo, useState} from 'react';
import {movedElementName} from "@/utils/scene/fea/elementInstances";
import {selectInOtherModel} from '@/utils/scene/crossModelSelect';
import type {LinkResult} from '@/state/lineageStore';
import {useViewerStores} from '@/state/AdaViewerContext';
import {useOptionsStore} from '@/state/optionsStore';
import RowKebabMenu, {KebabMenuItem} from '@/components/common/RowKebabMenu';
import {writeToClipboard} from '@/utils/clipboard/copySelectionNames';
import ConnectionsSection from './ConnectionsSection';
import MeshStatsSection from './MeshStatsSection';
import ProceduralSystemSection from './ProceduralSystemSection';
import ProceduralEquipmentSection from './ProceduralEquipmentSection';
import WeldRows, {isWeldMeta, type WeldMeta} from './WeldRows';
import {capabilities} from '@/services/capabilities';
import {fetchElementProps, type FeaElementProps} from '@/services/feaElementProps';
import {elementNodes} from '@/services/fea/feaFieldDetail';
import {useFeaAnimationStore} from '@/state/feaAnimationStore';
import {elementLabelNumber} from '@/utils/scene/fea/elementValues';
import {feaSourceScope} from '@/utils/scene/fea/streaming/session';

// Decimal-place choices offered in the Properties kebab menu for the "Clicked at" row. A small
// model (a few units across) needs more decimals before nearby clicks show a different position.
const DECIMAL_CHOICES = [1, 2, 3, 4, 6, 8];
const COPIED_FEEDBACK_MS = 1500;

const CheckIcon: React.FC = () => (
    <svg viewBox="0 0 16 16" className="w-3.5 h-3.5" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M3 8.5l3.5 3.5L13 4"/>
    </svg>
);

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

export type MaterialDict = {
    name?: string | null;
    E?: number | null;
    rho?: number | null;
    sig_y?: number | null;
    sig_u?: number | null;
    v?: number | null;
};

export type SectionDict = {
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

type CurvedPlateMeta = {
    type: 'PlateCurved';
    name?: string;
    thickness?: number | null;
    material?: MaterialDict | null;
};

type Props = {
    data: any | null;
    /** Plain rows, always open: no foldable "Properties" heading (`ObjectInfoBodyProps.flat`). */
    flat?: boolean;
    /** The summary is the panel's menu (`ObjectInfoBodyProps.summary: "menu"`): the clicked-at row and
     *  the mesh stats are in it, not here. */
    menuSummary?: boolean;
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

/** A section's dimensions as label / text pairs, in the units the rows show (mm). */
export function sectionFields(section: SectionDict): [string, string][] {
    const out: [string, string][] = [];
    if (section.type) out.push(["Profile", section.type]);
    if (section.h != null) out.push(["h", fmtMm(section.h)]);
    if (section.w_top != null || section.w_btn != null) {
        const b = section.w_top != null ? fmtMm(section.w_top) : "—";
        out.push(["b", section.w_btn != null && section.w_btn !== section.w_top ? `${b} / ${fmtMm(section.w_btn)}` : b]);
    }
    if (section.t_w != null) out.push(["t_w", fmtMm(section.t_w)]);
    if (section.t_ftop != null || section.t_fbtn != null) {
        const t = section.t_ftop != null ? fmtMm(section.t_ftop) : "—";
        out.push(["t_f", section.t_fbtn != null && section.t_fbtn !== section.t_ftop ? `${t} / ${fmtMm(section.t_fbtn)}` : t]);
    }
    if (section.r != null) out.push(["r", fmtMm(section.r)]);
    if (section.wt != null) out.push(["wt", fmtMm(section.wt)]);
    return out;
}

/** A material's constants as label / text pairs (GPa, MPa, kg/m³). */
export function materialFields(material: MaterialDict): [string, string][] {
    const out: [string, string][] = [];
    if (material.E != null) out.push(["E", fmtGpa(material.E)]);
    if (material.v != null) out.push(["ν", fmtNu(material.v)]);
    if (material.rho != null) out.push(["ρ", fmtDensity(material.rho)]);
    if (material.sig_y != null) out.push(["σ_y", fmtMpa(material.sig_y)]);
    if (material.sig_u != null) out.push(["σ_u", fmtMpa(material.sig_u)]);
    return out;
}

/** A thickness, as the rows show it. */
export const formatThickness = (v: number | null | undefined): string => fmtMm(v);

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

/** A picked FEA element's own data: its type, its nodes, and the section, thickness and
 *  material the analysis gave it. From the bake (AFEP sidecar + manifest tables + the
 *  element buckets' node indices), so it reads the same over REST as over the websocket;
 *  nothing here asks the server. Renders nothing for a name that is not an FEA element. */
/** What core knows of a picked FEA element (`name` its draw range, "E12"): its type, nodes,
 *  instance, thickness, section and material. Null for a name that is not an FEA element of the
 *  loaded result. Shared by the classic rows below and the flat `FeaElementSection`. */
export interface FeaElementData {
    label: number;
    elemType: string | null;
    typeName: string | null;
    instance: string | null;
    nodes: readonly number[];
    thickness: number | null;
    section: SectionDict | null;
    material: MaterialDict | null;
    /** The element's property record has been read (a section or material may still be absent). */
    hasProps: boolean;
}

export function useFeaElementData(name: string | null): FeaElementData | null {
    const manifest = useFeaAnimationStore((s) => s.manifest);
    const sourceName = useFeaAnimationStore((s) => s.sourceName);
    const label = name ? elementLabelNumber(name) : null;
    const isElement = !!name && /^E\d+$/.test(name) && label !== null && !!manifest;
    const [props, setProps] = useState<FeaElementProps | null>(null);

    React.useEffect(() => {
        setProps(null);
        const url = manifest?.mesh?.element_props_url;
        if (!isElement || !url || !sourceName || label === null) return;
        let live = true;
        const {fetcher, cacheKey} = capabilities.fea.fieldFetchers(feaSourceScope(sourceName), sourceName);
        void fetchElementProps(fetcher, url, `${cacheKey}::${url}`).then((table) => {
            if (live) setProps(table?.get(label) ?? null);
        });
        return () => {
            live = false;
        };
    }, [isElement, label, manifest, sourceName]);

    // Async: a split manifest (bake_version 6) keeps the element tables and the
    // node ids in sidecars, fetched once per source on the first pick.
    const [nodes, setNodes] = useState<{elemType: string; nodes: number[]} | null>(null);
    React.useEffect(() => {
        setNodes(null);
        if (!isElement || !manifest || !sourceName || label === null) return;
        let live = true;
        const {fetcher, cacheKey} = capabilities.fea.fieldFetchers(feaSourceScope(sourceName), sourceName);
        void elementNodes(manifest, label, fetcher, cacheKey)
            .then((found) => {
                if (live) setNodes(found);
            })
            .catch(() => undefined);
        return () => {
            live = false;
        };
    }, [isElement, label, manifest, sourceName]);

    if (!isElement || !manifest || label === null) return null;
    const tables = manifest.element_properties ?? {};
    return {
        label,
        elemType: nodes?.elemType ?? null,
        typeName: (props?.eltype ? tables.element_types?.[String(props.eltype)] : undefined) ?? (props?.eltype ? `type ${props.eltype}` : null),
        instance: movedElementName(manifest, label),
        nodes: nodes?.nodes ?? [],
        thickness: props && Number.isFinite(props.thickness) ? props.thickness : null,
        section: props?.section ? tables.sections?.[String(props.section)] ?? null : null,
        material: props?.material ? tables.materials?.[String(props.material)] ?? null : null,
        hasProps: !!props,
    };
}

const FeaElementRows: React.FC<{name: string | null}> = ({name}) => {
    const data = useFeaElementData(name);
    if (!data) return null;
    const {elemType, typeName, instance: instanceLabel, nodes, thickness, section, material} = data;
    const props = data.hasProps;
    return (
        <>
            <Row label="Element:">
                {elemType ?? '—'}
                {typeName ? ` (${typeName})` : ''}
            </Row>
            {instanceLabel && <Row label="Instance:">{instanceLabel}</Row>}
            {nodes.length > 0 && <Row label="Nodes:">{nodes.join(', ')}</Row>}
            {thickness !== null && <Row label="Thickness:">{fmtMm(thickness)}</Row>}
            {section && <SectionRows section={section} />}
            {props && <MaterialRows material={material} />}
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
                    className="ml-2 bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white text-[11px] rounded-sm px-2 py-0.5"
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
                        className="ml-2 bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white text-[11px] rounded-sm px-2 py-0.5"
                        title={`Switch to ${entry.file} and select all ${entry.elementNames.length} elements`}
                    >
                        Select FEA elements
                    </button>
                </Row>
            ))}
        </>
    );
};

/** `menu`: drawn after the coordinates (the flat panel's decimals menu, which has no heading to sit on). */
/** Where the pick was clicked, as shown ("(1.264, 0.500, 0.402)") and as copied (the same numbers,
 *  comma-separated, no parentheses, so they paste straight into code); null without a click. */
export function useClickedPosition(): {display: string; copy: string} | null {
    const {useObjectInfoStore, useModelState} = useViewerStores();
    const clickCoord = useObjectInfoStore((s) => s.clickCoordinate);
    const zIsUp = useModelState((s) => s.zIsUp);
    const decimals = useOptionsStore((s) => s.clickedCoordDecimals);
    if (!clickCoord) return null;
    const x = clickCoord.x.toFixed(decimals);
    const y = clickCoord.y.toFixed(decimals);
    const z = clickCoord.z.toFixed(decimals);
    // Mirror the original axis-swap convention from CoordinateDisplay:
    // viewer scene is y-up by default, but adapy's Z-up world is what
    // users think in — so when zIsUp is set, show the world tuple
    // (x, y, z) directly; otherwise re-order to compensate.
    const ordered = zIsUp ? [x, y, z] : [x, z, y];
    return {display: `(${ordered.join(', ')})`, copy: ordered.join(', ')};
}

const ClickedAtRow: React.FC<{menu?: React.ReactNode}> = ({menu}) => {
    const position = useClickedPosition();
    const [copied, setCopied] = useState(false);
    if (!position) return null;
    const {display} = position;
    const onCopy = () => {
        void writeToClipboard(position.copy).then((ok) => {
            if (!ok) return;
            setCopied(true);
            window.setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS);
        });
    };
    return (
        <Row label="Clicked at:">
            <span className="inline-flex items-center gap-1" data-clicked-at="">
                <button
                    type="button"
                    onClick={onCopy}
                    className="text-left text-gray-100 hover:text-white cursor-pointer"
                    title="Copy coordinates as comma-separated floats"
                >
                    {copied ? `${display} ✓` : display}
                </button>
                {menu}
            </span>
        </Row>
    );
};

// Source face of the last click — shown only when face-level picking is on and a region resolved.
// The #id is the STEP ADVANCED_FACE / IFC IfcFace entity id, so a mis-tessellated face can be named
// exactly (not just "somewhere on this solid"); copyable for pasting into a bug report.
const ClickedFaceRow: React.FC = () => {
    const {useObjectInfoStore} = useViewerStores();
    const face = useObjectInfoStore((s) => s.clickedFace);
    const [copied, setCopied] = useState(false);
    if (!face) return null;
    const label = `#${face.faceId} (face ${face.seq})`;
    const onCopy = () => {
        void writeToClipboard(`#${face.faceId}`).then((ok) => {
            if (!ok) return;
            setCopied(true);
            window.setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS);
        });
    };
    return (
        <Row label="Face:">
            <button
                type="button"
                onClick={onCopy}
                className="text-left text-gray-100 hover:text-white cursor-pointer"
                title="Copy the source face id"
            >
                {copied ? `${label} ✓` : label}
            </button>
        </Row>
    );
};

const ObjectMetadataPanel: React.FC<Props> = ({data, flat = false, menuSummary = false}) => {
    const {useObjectInfoStore, useLineageStore} = useViewerStores();
    const showMeshStats = useOptionsStore((s) => s.showMeshStats);
    // Default collapsed: most clicks are just for selection / hide /
    // jump, not for inspecting properties. Folding keeps the info box
    // compact and the chevron tells the user where the data lives.
    // Open from the start on an FEA result, though: there the properties ARE what a click
    // on an element is for -- its section, thickness and material.
    // Flat: always open, there is no heading to fold it under.
    const [expandedState, setExpanded] = useState(() => !!useFeaAnimationStore.getState().manifest);
    const expanded = flat || expandedState;
    // The link is derived live from the lineage store and the current
    // selection (file + name) — keeps it reactive to file load/unload
    // without going back through the server.
    const fileName = useObjectInfoStore((s) => s.fileName);
    const clickedName = useObjectInfoStore((s) => s.name);
    // The link is a new object each time it is worked out, so it is worked out when what it is made
    // from changes, not in the store's selector (which must give the same value for the same state).
    const lineage = useLineageStore((s) => s.entries);
    const link = useMemo(
        () => useLineageStore.getState().findLink(fileName, clickedName),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [lineage, fileName, clickedName],
    );
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
    const meta = (effectiveData ?? null) as BeamMeta | PlateMeta | CurvedPlateMeta | WeldMeta | null;
    const weld = isWeldMeta(meta);
    const known = meta?.type === 'Beam' || meta?.type === 'Plate' || meta?.type === 'PlateCurved' || weld;
    const decimals = useOptionsStore((s) => s.clickedCoordDecimals);
    const setDecimals = useOptionsStore((s) => s.setClickedCoordDecimals);
    const decimalItems: KebabMenuItem[] = DECIMAL_CHOICES.map((d) => ({
        key: `dec-${d}`,
        label: `${d} decimal${d === 1 ? '' : 's'}`,
        // A check on the active choice; a blank same-width spacer keeps the labels aligned.
        icon: d === decimals ? <CheckIcon /> : <span className="inline-block w-3.5" />,
        onClick: () => setDecimals(d),
    }));
    const decimalsMenu = (
        <RowKebabMenu
            ariaLabel="Properties display options"
            header="Clicked-at decimals"
            items={decimalItems}
            buttonClassName="h-5 w-5"
        />
    );
    return (
        <div className={flat ? "mt-1" : "mt-2"} data-object-metadata={flat ? "flat" : ""}>
            {!flat && (
                <div className="flex items-center gap-1">
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
                    {decimalsMenu}
                </div>
            )}
            {expanded && (
                <div id="object-properties" className={flat ? "table" : "mt-1 ml-4 table"}>
                    {meta && !known && <Row label="Type:">{(meta as any).type ?? 'Unknown'}</Row>}
                    {meta && !known && <Row label="Info:">No metadata available</Row>}
                    {!known && !flat && <FeaElementRows name={clickedName} />}
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
                    {weld && <WeldRows meta={meta} fileName={fileName} weldName={clickedName} />}
                    {meta?.type === 'PlateCurved' && (
                        <>
                            <Row label="Type:">Curved Plate</Row>
                            <Row label="Thickness:">{fmtMm((meta as CurvedPlateMeta).thickness)}</Row>
                            <MaterialRows material={meta.material} />
                        </>
                    )}
                    {/* Flat: the decimals menu goes with the row it formats. */}
                    {!menuSummary && <ClickedAtRow menu={flat ? decimalsMenu : undefined} />}
                    <ClickedFaceRow />
                    {link && <LinkRow link={link} />}
                </div>
            )}
            {expanded && showMeshStats && !menuSummary && (
                <div className={flat ? "" : "ml-4"}>
                    <MeshStatsSection />
                </div>
            )}
            <ConnectionsSection fileName={fileName} objectName={clickedName} />
            <ProceduralSystemSection objectName={clickedName} />
            <ProceduralEquipmentSection objectName={clickedName} />
        </div>
    );
};

export default ObjectMetadataPanel;
