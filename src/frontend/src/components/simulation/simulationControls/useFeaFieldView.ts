// What the FEA result controls offer for the shown field: its metadata, the component
// (reduction) options, the field picker's hierarchy, the step list with the load combinations
// computed on request and their status, and which element-field pickers apply. Derived from the
// animation store's manifest and selection; no state of its own.

import { useMemo, useSyncExternalStore } from "react";

import { caseStatus, caseStatusVersion, subscribeCaseStatus } from "@/services/fea/feaCaseResolver";
import type { useFeaAnimationStore } from "@/state/feaAnimationStore";
import { unbakedStepsNote } from "@/utils/scene/fea/bakedSteps";
import { caseStatusText, hasLazyCases, mergeCaseSteps } from "@/utils/scene/fea/caseSteps";
import { buildFeaPropertyFields, buildFeaResultHierarchy } from "@/utils/scene/fea/resultHierarchy";
import { availableResultLayers } from "@/utils/scene/fea/resultLayers";
import { selectedResultUnit } from "@/utils/scene/fea/resultUnits";
import { formatStepTime } from "@/utils/scene/fea/timeHistory";

type FeaAnimationState = ReturnType<typeof useFeaAnimationStore.getState>;

export function useFeaFieldView({
    manifest,
    fieldName,
    reduction,
    stepIndex,
    timeHistory,
    sourceName,
}: Pick<FeaAnimationState, "manifest" | "fieldName" | "reduction" | "stepIndex" | "timeHistory" | "sourceName">) {
    // Active field metadata. Drives the reduction dropdown options
    // (magnitude only available for vector fields) and the step
    // slider's max.
    const activeField = useMemo(() => {
        if (!manifest || !fieldName) return null;
        return manifest.fields.find((f) => f.name_canonical === fieldName) ?? null;
    }, [manifest, fieldName]);
    const fieldPickerValue = activeField?.surface_variants?.[0]?.field_name
        ?? fieldName
        ?? "";
    const activeUnit = selectedResultUnit(activeField, reduction);
    const stepValues = useMemo(() => activeField?.steps.map((s) => s.value) ?? [], [activeField]);
    const stepTimeText = timeHistory
        ? formatStepTime(activeField?.steps[stepIndex]?.value ?? 0, stepValues)
        : "";
    const timeReadoutChars = timeHistory && stepValues.length
        ? formatStepTime(Math.max(...stepValues.map(Math.abs)), stepValues).length
        : 0;

    const reductionOptions = useMemo<string[]>(() => {
        if (!activeField) return [];
        const out: string[] = [];
        if (!activeField.group_path && activeField.kind.startsWith("vector")) out.push("magnitude");
        for (const c of activeField.components) out.push(c);
        return out;
    }, [activeField]);

    const fieldHierarchy = useMemo(
        () => buildFeaResultHierarchy(manifest?.fields ?? []),
        [manifest],
    );
    // Colour by what the elements ARE (superelement, material, section, thickness):
    // listed apart from the results, after them.
    const propertyFields = useMemo(() => buildFeaPropertyFields(manifest?.fields ?? []), [manifest]);
    // The step list: stored steps, then (bake_version 4) the load combinations
    // the bake left to compute on request, each with its status.
    const lazyCases = hasLazyCases(manifest);
    const stepSlots = useMemo(() => mergeCaseSteps(manifest, activeField), [manifest, activeField]);
    const caseStatusTick = useSyncExternalStore(subscribeCaseStatus, caseStatusVersion, caseStatusVersion);
    const slotStatus = useMemo(() => {
        void caseStatusTick;
        const out = new Map<number, string>();
        if (!sourceName) return out;
        for (const s of stepSlots) {
            if (s.kind !== "combination" || !s.recipeHash) continue;
            out.set(s.slot, caseStatusText(caseStatus(sourceName, {n: s.value, recipe_hash: s.recipeHash})));
        }
        return out;
    }, [stepSlots, sourceName, caseStatusTick]);
    const activeSlot = stepSlots[stepIndex];
    // A bake of chosen steps (or of the model only) leaves cases out, and the
    // pickers below cannot list what is not there: say so, once. A bake that
    // offers its combinations on request lists them instead, so it needs no note.
    const unbakedNote = useMemo(() => (lazyCases ? null : unbakedStepsNote(manifest)), [manifest, lazyCases]);

    // Element-field path: expose Surface/layer and IP reduction pickers when
    // the active field has per_type buckets. Exact selectable surfaces omit
    // the generic "all" reduction because mixing fibres is misleading.
    const isElemField = !!(activeField?.per_type && activeField.per_type.length > 0);
    const layerOptions = useMemo<string[]>(
        () => activeField && isElemField ? availableResultLayers(activeField) : [],
        [activeField, isElemField],
    );
    const hasExactSurface = activeField?.surface === "selectable";
    const showIpReduction = isElemField
        && activeField?.support !== "element_nodal"
        && activeField?.support !== "element_average";
    const ipReductionOptions = ["max_abs", "max", "min", "mean"];
    return {
        activeField,
        fieldPickerValue,
        activeUnit,
        stepTimeText,
        timeReadoutChars,
        reductionOptions,
        fieldHierarchy,
        propertyFields,
        lazyCases,
        stepSlots,
        slotStatus,
        activeSlot,
        unbakedNote,
        isElemField,
        layerOptions,
        hasExactSurface,
        showIpReduction,
        ipReductionOptions,
    };
}
