// How "Download as…" writes its files -- the export configuration panel's choices, sent with every
// selection export as `export_options` (`ada/comms/rest/selection_export.py`'s
// `parse_export_options`). Kept in this browser, like the other options panels' stores. The name
// prefixes are not sent: they name the saved file only (`utils/exportFileName`).

import {create} from "zustand";
import {persist} from "zustand/middleware";

/** The STEP application protocols the server writes. AP214 is its default. */
export type StepSchema = "AP214" | "AP242";

export const STEP_SCHEMAS: readonly {readonly value: StepSchema; readonly label: string; readonly title: string}[] = [
    {
        value: "AP214",
        label: "AP214",
        title: "Automotive design (AUTOMOTIVE_DESIGN): the long-standing default, read by every CAD system",
    },
    {
        value: "AP242",
        label: "AP242",
        title: "Managed model-based 3D engineering: the current STEP protocol, superseding AP203 and AP214",
    },
];

/** The IFC schemas the server writes. IFC4 is its default. */
export type IfcSchema = "IFC4" | "IFC4X3";

export const IFC_SCHEMAS: readonly {readonly value: IfcSchema; readonly label: string; readonly title: string}[] = [
    {value: "IFC4", label: "IFC4", title: "IFC4 ADD2 TC1: the version most applications read"},
    {value: "IFC4X3", label: "IFC4x3", title: "IFC4x3 ADD2: the current schema, extending IFC4"},
];

export interface ExportOptions {
    readonly stepSchema: StepSchema;
    readonly ifcSchema: IfcSchema;
    /** Write cylinders and cones as swept solids, for readers that cannot convert primitive solids. */
    readonly ifcSweptPrimitives: boolean;
    /** Start the saved file's name with the project the selection is from. */
    readonly prefixProject: boolean;
    /** Start the saved file's name with this (after the project); empty for none. */
    readonly customPrefix: string;
    /** End the saved file's name with the selection's own name. */
    readonly includeName: boolean;
}

export const DEFAULT_EXPORT_OPTIONS: ExportOptions = {
    stepSchema: "AP214",
    ifcSchema: "IFC4",
    ifcSweptPrimitives: false,
    prefixProject: false,
    customPrefix: "",
    includeName: true,
};

interface ExportOptionsState extends ExportOptions {
    setStepSchema: (schema: StepSchema) => void;
    setIfcSchema: (schema: IfcSchema) => void;
    setIfcSweptPrimitives: (on: boolean) => void;
    setPrefixProject: (on: boolean) => void;
    setCustomPrefix: (prefix: string) => void;
    setIncludeName: (on: boolean) => void;
    reset: () => void;
}

export const useExportOptionsStore = create<ExportOptionsState>()(
    persist(
        (set) => ({
            ...DEFAULT_EXPORT_OPTIONS,
            setStepSchema: (stepSchema) => set({stepSchema}),
            setIfcSchema: (ifcSchema) => set({ifcSchema}),
            setIfcSweptPrimitives: (ifcSweptPrimitives) => set({ifcSweptPrimitives}),
            setPrefixProject: (prefixProject) => set({prefixProject}),
            setCustomPrefix: (customPrefix) => set({customPrefix}),
            setIncludeName: (includeName) => set({includeName}),
            reset: () => set({...DEFAULT_EXPORT_OPTIONS}),
        }),
        {
            name: "ada-export-options",
            partialize: (s) => ({
                stepSchema: s.stepSchema,
                ifcSchema: s.ifcSchema,
                ifcSweptPrimitives: s.ifcSweptPrimitives,
                prefixProject: s.prefixProject,
                customPrefix: s.customPrefix,
                includeName: s.includeName,
            }),
        },
    ),
);

/** The route's `export_options` for `options`. */
export function exportOptionsWire(options: ExportOptions): {
    step_schema: StepSchema;
    ifc_schema: IfcSchema;
    ifc_swept_primitives: boolean;
} {
    return {
        step_schema: options.stepSchema,
        ifc_schema: options.ifcSchema,
        ifc_swept_primitives: options.ifcSweptPrimitives,
    };
}

/** Whether `options` differ from the defaults -- what the panel's button marks. */
export function exportOptionsChanged(options: ExportOptions): boolean {
    return (
        options.stepSchema !== DEFAULT_EXPORT_OPTIONS.stepSchema ||
        options.ifcSchema !== DEFAULT_EXPORT_OPTIONS.ifcSchema ||
        options.ifcSweptPrimitives !== DEFAULT_EXPORT_OPTIONS.ifcSweptPrimitives ||
        options.prefixProject !== DEFAULT_EXPORT_OPTIONS.prefixProject ||
        options.customPrefix.trim() !== DEFAULT_EXPORT_OPTIONS.customPrefix ||
        options.includeName !== DEFAULT_EXPORT_OPTIONS.includeName
    );
}
