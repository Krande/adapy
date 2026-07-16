import React, {useCallback, useEffect, useState} from "react";

import {SerializerTessellatorSelect} from "@/components/convert/SerializerTessellatorSelect";
import {convertWithSelection} from "@/services/conversion";
import {SerializerSelection, boolOptionFor, boolOptionSupported} from "@/services/conversion/serializerMatrix";
import {viewerApi} from "@/services/viewerApi";
import {useModelState} from "@/state/modelState";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

// Human name + schema/version for a source file. Extension gives the format; the schema/version
// (STEP AP214/AP242, IFC4/IFC2X3, Genie XML) is only in the file HEADER, so we sniff the first few
// KB of the source blob. Kept entirely client-side (no backend round-trip) — the source bytes are
// already fetchable, and a header sniff is far cheaper than a whole-file download.
const FORMAT_NAMES: Record<string, string> = {
    ".step": "STEP",
    ".stp": "STEP",
    ".ifc": "IFC",
    ".xml": "Genie XML",
    ".sat": "ACIS",
    ".acis": "ACIS",
    ".stl": "STL",
    ".obj": "OBJ",
    ".glb": "glTF",
    ".gltf": "glTF",
    ".fem": "Sesam FEM",
    ".inp": "Abaqus",
};

function extOf(name: string): string {
    const i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i).toLowerCase();
}

function fmtSize(bytes: number | null): string {
    if (bytes === null) return "—";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Parse a schema/version label out of the header text of a STEP/IFC/Genie source. Best-effort:
// returns null when nothing recognisable is present (the caller then shows just the format name).
function sniffVersion(ext: string, header: string): string | null {
    if (ext === ".step" || ext === ".stp") {
        // FILE_SCHEMA (('AUTOMOTIVE_DESIGN { 1 0 10303 214 ... }'));  — the 10303 NNN and the schema
        // name both identify the AP. Prefer the explicit AP name, fall back to the number.
        const m = header.match(/FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'/i);
        if (!m) return null;
        const s = m[1].toUpperCase();
        if (s.includes("AP242") || /10303\s+242/.test(s)) return "AP242";
        if (s.includes("CONFIG_CONTROL_DESIGN") || /10303\s+203/.test(s)) return "AP203";
        if (s.includes("AUTOMOTIVE_DESIGN") || /10303\s+214/.test(s)) return "AP214";
        const num = s.match(/10303\s+(\d+)/);
        return num ? `AP${num[1]}` : null;
    }
    if (ext === ".ifc") {
        // FILE_SCHEMA(('IFC4')); / ('IFC2X3') / ('IFC4X3_ADD2')
        const m = header.match(/FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'/i);
        return m ? m[1].toUpperCase() : null;
    }
    if (ext === ".xml") {
        // Genie XML carries a program/version on the root or a metadata element; surface whatever
        // version attribute we find first (e.g. <... version="V8.4-...">).
        const m = header.match(/version\s*=\s*"([^"]+)"/i);
        return m ? m[1] : null;
    }
    return null;
}

const SourceSection = () => {
    const loadedSourceName = useModelState((s) => s.loadedSourceName);
    const scope = useScopeStore((s) => s.current);
    const scopePart = scope ? scopeUrlPart(scope) : "";

    const [size, setSize] = useState<number | null>(null);
    const [version, setVersion] = useState<string | null>(null);
    const [serializerSel, setSerializerSel] = useState<SerializerSelection>({});
    const [faceRegions, setFaceRegions] = useState(false);
    const [reconverting, setReconverting] = useState(false);
    const [msg, setMsg] = useState<string | null>(null);

    const ext = loadedSourceName ? extOf(loadedSourceName) : "";
    const format = FORMAT_NAMES[ext] ?? (ext ? ext.slice(1).toUpperCase() : "—");

    // Fetch size (scope listing) + schema/version (header sniff) whenever the loaded source changes.
    useEffect(() => {
        let cancelled = false;
        setSize(null);
        setVersion(null);
        if (!loadedSourceName || !scopePart) return;
        (async () => {
            try {
                const files = await viewerApi.listFiles(scopePart);
                const hit = files.find((f) => f.key === loadedSourceName);
                if (!cancelled) setSize(hit?.size ?? null);
            } catch {
                /* size is best-effort */
            }
            // Only text-header formats carry a sniffable schema/version.
            if ([".step", ".stp", ".ifc", ".xml"].includes(ext)) {
                try {
                    const {buf} = await viewerApi.getBlobRange(scopePart, loadedSourceName, 0, 8192);
                    const text = new TextDecoder("utf-8", {fatal: false}).decode(buf);
                    if (!cancelled) setVersion(sniffVersion(ext, text));
                } catch {
                    /* version is best-effort */
                }
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [loadedSourceName, scopePart, ext]);

    const faceRegionsSupported = boolOptionSupported(ext, "glb", "face_regions", serializerSel);
    const faceRegionsOn = faceRegions && faceRegionsSupported;
    const hasFaceRegionsOpt = boolOptionFor(ext, "glb", "face_regions") !== null;

    const reconvert = useCallback(async () => {
        if (!loadedSourceName || reconverting) return;
        setReconverting(true);
        setMsg("re-converting…");
        try {
            const derivedKey = await convertWithSelection(scopePart, loadedSourceName, "glb", {
                selection: serializerSel,
                extraOptions: faceRegionsOn ? {face_regions: true} : undefined,
                reconvert: true,
            });
            const {clear_loaded_model} = await import("@/utils/scene/handlers/clear_loaded_model");
            await clear_loaded_model();
            const {overlay_file_in_scene} = await import("@/utils/scene/handlers/overlay_file_in_scene");
            await overlay_file_in_scene(loadedSourceName, derivedKey, {scope: scopePart});
            setMsg("loaded fresh conversion");
        } catch (e) {
            setMsg(`failed: ${e instanceof Error ? e.message : String(e)}`);
        } finally {
            setReconverting(false);
        }
    }, [loadedSourceName, reconverting, scopePart, serializerSel, faceRegionsOn]);

    if (!loadedSourceName) {
        return <div className="text-sm text-gray-400 px-1 py-2">No source model loaded.</div>;
    }

    return (
        <div className="text-sm px-1 py-1 space-y-1">
            <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
                <span className="text-gray-400">Type</span>
                <span className="font-medium">{version ? `${format} (${version})` : format}</span>
                <span className="text-gray-400">File</span>
                <span className="truncate" title={loadedSourceName}>
                    {loadedSourceName}
                </span>
                <span className="text-gray-400">Size</span>
                <span>{fmtSize(size)}</span>
            </div>

            <div className="pt-1 border-t border-gray-700">
                <div className="text-gray-400 mb-1">Re-convert</div>
                <SerializerTessellatorSelect
                    ext={ext}
                    target="glb"
                    value={serializerSel}
                    onChange={setSerializerSel}
                    disabled={reconverting}
                    compact
                />
                {hasFaceRegionsOpt && (
                    <label className="flex items-center gap-1 mt-1">
                        <input
                            type="checkbox"
                            checked={faceRegions}
                            disabled={reconverting || !faceRegionsSupported}
                            onChange={(e) => setFaceRegions(e.target.checked)}
                        />
                        <span className={faceRegionsSupported ? "" : "text-gray-500"}>Clickable surfaces</span>
                    </label>
                )}
                <button
                    className="mt-1 w-full rounded-sm px-2 py-1 bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-gray-100"
                    disabled={reconverting}
                    onClick={reconvert}
                >
                    {reconverting ? "re-converting…" : "Re-convert"}
                </button>
                {msg && <div className="mt-1 text-xs text-gray-400">{msg}</div>}
            </div>
        </div>
    );
};

export default SourceSection;
