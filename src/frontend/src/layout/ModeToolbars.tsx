// Core's own mode toolbars: ready-made content for a shell mode's toolbar slot
// (`UiShellMode.toolbars`, drawn by `ShellModeToolbar`), or mounted directly in
// a shell's own chrome. Each is built over core commands and stores, so it
// shows what the palette, the menus and the Simulation panel show, and a fix
// to one reaches all.
//
//   playback        play / pause, stop, step, the step slider, the period and the deformation scale
//   section         section planes: one per axis, on / off, flip, the offset slider, remove
//   representation  edges, an FEA result's undeformed outline, element edges and colours, node / element number labels
//   colorBy         colour the scene by a property (an FEA section / material / thickness, the models), and reset
//   build           the procedural builder: model name and revision, undo / redo, compile, commit, close,
//                   and the tools: placement (cell, loft, opening and equipment with their type pickers),
//                   the move / rotate / resize gizmos, the view (topology / simulation / detail, result over
//                   or beside the topology); each part is a component of its own too
//
// Core has no scene-wide wireframe, transparency or annotation (measure, label,
// markup) tools to put in a toolbar; there is no group for them.

import React from "react";
import {useStore} from "zustand";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useModelState} from "@/state/modelState";
import {useOptionsStore} from "@/state/optionsStore";
import {useSectionStore} from "@/state/sectionStore";
import {hasEmbeddedDoc, useCellBuilderStore} from "@/state/cellBuilderStore";
import {useColorByStore} from "@/state/colorByStore";
import {useLabelsStore} from "@/state/labelsStore";
import {typePickerItems} from "@/utils/cellbuilder/ports";
import {
  applyColorBy,
  colorBySources,
  colorBySourcesVersion,
  resetColorBy,
  subscribeColorBySources,
} from "@/utils/scene/colorBy";
import {setResultScale, setResultStep} from "@/utils/scene/fea/playbackActions";

import {CORE_COMMAND_IDS as ID} from "./coreCommandIds";
import {PauseIcon, PlayIcon} from "./commandIcons";
import {CommandButton, type CommandButtonProps} from "./CommandButton";
import {SplitButton, type SplitButtonItem} from "./SplitButton";
import {workingPlane} from "./coreCommands";
import {CORE_MODE_TOOLBAR_IDS, type CoreModeToolbarId} from "./shellModes";

export {CommandButton};
export type {CommandButtonProps};

const BAR = "flex items-center gap-0.5 text-xs";
const FIELD = "h-6 rounded-sm bg-white px-1 text-black";
const DIVIDER = <span aria-hidden="true" className="mx-1 h-4 w-px bg-white/20" />;

/** Play / pause, stop, step back and forward, the step slider, the period and the deformation scale of a result. */
export function PlaybackToolbar({className = BAR}: {className?: string}) {
  const playing = useFeaAnimationStore((s) => s.isPlaying);
  const active = useFeaAnimationStore((s) => s.sessionActive);
  const stepIndex = useFeaAnimationStore((s) => s.stepIndex);
  const nSteps = useFeaAnimationStore((s) => s.nSteps);
  const timeHistory = useFeaAnimationStore((s) => s.timeHistory);
  const period = useFeaAnimationStore((s) => s.period);
  const setPeriod = useFeaAnimationStore((s) => s.setPeriod);
  const scale = useFeaAnimationStore((s) => s.scaleFactor);
  return (
    <div className={className} role="group" aria-label="Result playback" data-mode-toolbar="playback">
      <CommandButton id={ID.stepBack} />
      <CommandButton id={ID.playPause} icon={playing ? PauseIcon : PlayIcon} label={playing ? "Pause results" : "Play results"} pressed={playing} />
      <CommandButton id={ID.stopResults} />
      <CommandButton id={ID.stepForward} />
      {nSteps > 1 && (
        <>
          <input
            type="range"
            className="mx-1 w-32"
            min={0}
            max={nSteps - 1}
            step={1}
            value={Math.min(stepIndex, nSteps - 1)}
            aria-label="Result step"
            onChange={(e) => setResultStep(Number(e.target.value))}
          />
          <span className="min-w-12 tabular-nums" data-playback-step="">
            {Math.min(stepIndex, nSteps - 1) + 1} / {nSteps}
          </span>
        </>
      )}
      {/* A time history plays its own time axis; only a mode shape or a static step oscillates, so only it has a period. */}
      {active && !timeHistory && (
        <label className="ml-1 flex items-center gap-1" title="Seconds per oscillation when playing">
          Period
          <input
            type="number"
            className={`${FIELD} w-14`}
            min={0.1}
            step={0.5}
            value={period}
            aria-label="Oscillation period in seconds"
            onChange={(e) => setPeriod(parseFloat(e.target.value))}
          />
        </label>
      )}
      {active && (
        <label className="ml-1 flex items-center gap-1" title="Deformation scale">
          Scale
          <input
            type="number"
            className={`${FIELD} w-16`}
            min={0}
            step="any"
            value={scale}
            aria-label="Deformation scale"
            onChange={(e) => setResultScale(parseFloat(e.target.value))}
          />
        </label>
      )}
    </div>
  );
}

/**
 * Where a section plane sits along its axis, and the model's extent there (the offset slider's
 * range). The plane is normal . x + constant = 0, so on its axis it sits at -constant / sign.
 * With no plane the Z axis; with no model, -1 to 1.
 */
export function sectionOffset(
  plane: {normal: readonly number[]; constant: number} | undefined,
  box: {min: {x: number; y: number; z: number}; max: {x: number; y: number; z: number}} | null,
): {axis: 0 | 1 | 2; sign: 1 | -1; offset: number; lo: number; hi: number} {
  const n = plane?.normal ?? [0, 0, 1];
  const axis = n.reduce((best, v, i) => (Math.abs(v) > Math.abs(n[best]) ? i : best), 0) as 0 | 1 | 2;
  const sign = n[axis] < 0 ? -1 : 1;
  const lo = box ? [box.min.x, box.min.y, box.min.z][axis] : -1;
  const hi = box ? [box.max.x, box.max.y, box.max.z][axis] : 1;
  return {axis, sign, offset: plane ? -plane.constant * sign : 0, lo, hi};
}

/** Section planes: one per axis, on / off, flip, the offset along the axis, remove all. */
export function SectionToolbar({className = BAR}: {className?: string}) {
  // Re-render on every change to the planes.
  useStore(useSectionStore);
  const box = useModelState((s) => s.boundingBox);
  const plane = workingPlane();
  const {sign, offset, lo, hi} = sectionOffset(plane, box);
  const span = hi - lo;
  return (
    <div className={className} role="group" aria-label="Section plane" data-mode-toolbar="section">
      {([ID.sectionX, ID.sectionY, ID.sectionZ] as const).map((id) => (
        <CommandButton key={id} id={id}>
          <span className="ml-0.5 font-semibold">{id.slice(-1).toUpperCase()}</span>
        </CommandButton>
      ))}
      {DIVIDER}
      <CommandButton id={ID.sectionToggle} />
      <CommandButton id={ID.sectionFlip} />
      {plane && box && span > 0 && (
        <input
          type="range"
          className="mx-1 w-32"
          min={lo}
          max={hi}
          step={span / 500}
          value={Math.min(hi, Math.max(lo, offset))}
          aria-label={`Section plane ${plane.label} offset`}
          onChange={(e) => useSectionStore.getState().setConstant(plane.id, -sign * Number(e.target.value))}
        />
      )}
      <CommandButton id={ID.sectionClear} />
    </div>
  );
}

/** How the model is drawn: geometry edges, and a loaded FEA result's undeformed outline, element edges and colours. */
export function RepresentationToolbar({className = BAR}: {className?: string}) {
  // Re-render when any of what the buttons show changes.
  useOptionsStore((s) => s.showEdges);
  useFeaAnimationStore((s) => `${s.sessionActive}${s.showUndeformed}${s.elementEdgesVisible}${s.resultColorsVisible}${s.warpEnabled}`);
  useLabelsStore((s) => `${s.nodes}${s.elements}${s.values}`);
  return (
    <div className={className} role="group" aria-label="Representation" data-mode-toolbar="representation">
      <CommandButton id={ID.edges} />
      <CommandButton id={ID.feaToggleContextWireframe} />
      {DIVIDER}
      <CommandButton id={ID.feaToggleResultColors} />
      <CommandButton id={ID.feaToggleElementEdges} />
      <CommandButton id={ID.feaToggleUndeformed} />
      <CommandButton id={ID.warp} />
      {DIVIDER}
      <CommandButton id={ID.labelsNodes} />
      <CommandButton id={ID.labelsElements} />
      <CommandButton id={ID.labelsValues} />
    </div>
  );
}

/**
 * Colour the scene by a property: a picker over what the scene can be coloured by now (an FEA
 * result's section, material and thickness; the loaded models; sources a plugin registers), and
 * a reset. The legend is the colour legend's (an FEA property) or `ColorByLegend` (the rest).
 */
export function ColorByToolbar({className = BAR}: {className?: string}) {
  React.useSyncExternalStore(subscribeColorBySources, colorBySourcesVersion, colorBySourcesVersion);
  const active = useColorByStore((s) => s.activeId);
  // Re-read the sources when a result loads or models come and go.
  useFeaAnimationStore((s) => `${s.sessionActive}${s.manifest ? s.manifest.fields.length : 0}`);
  const sources = colorBySources();
  return (
    <div className={className} role="group" aria-label="Colour by" data-mode-toolbar="colorBy">
      <label className="flex items-center gap-1" title="Colour the scene by a property">
        Colour by
        <select
          className={`${FIELD} max-w-40`}
          aria-label="Colour by"
          value={active ?? ""}
          disabled={!sources.length}
          onChange={(e) => void (e.target.value ? applyColorBy(e.target.value) : resetColorBy())}
        >
          <option value="">{sources.length ? "None" : "Nothing to colour by"}</option>
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
      <CommandButton id={ID.colorByReset} />
    </div>
  );
}

/** Re-render when anything the builder's buttons show changes (the model, history, compile, the tools, the view). */
function useBuilderKey(): void {
  useCellBuilderStore(
    (s) =>
      `${s.active?.name ?? ""}|${s.active?.revision ?? ""}|${s.dirty}|${s.past.length}|${s.future.length}|${s.compileJob?.status ?? ""}|${s.committing}` +
      `|${s.mode}|${s.gizmoMode}|${s.repMode}|${s.superimpose}|${s.sideBySide}|${s.selection?.cellId ?? ""}` +
      `|${s.openingTypes.length}|${s.equipmentTypes.length}|${s.selectedOpeningType ?? ""}|${s.selectedEquipmentType ?? ""}|${Object.keys(s.cells).length}`,
  );
}

/** The label of the type `slug` among `types`, null when none is picked (or the pick is gone from the list). */
function pickedTypeLabel(types: {slug: string; name: string; origin: string}[], slug: string | null): string | null {
  return typePickerItems(types).find((t) => t.slug === slug)?.label ?? null;
}

/**
 * Placement: add a cell, a loft member, an opening or a piece of equipment. The last two are split
 * buttons: the caret picks the type, the main half places the type picked last (and opens the picker
 * while none is picked yet). Equipment's picker also seats a piece onto or into an existing cell.
 */
export function BuilderPlacementTools() {
  useBuilderKey();
  const s = useCellBuilderStore.getState();
  const hasCells = Object.values(s.cells).some((c) => c.kind === "cell");
  const opening: SplitButtonItem[] = s.openingTypes.length
    ? typePickerItems(s.openingTypes).map((t) => ({
        key: t.key,
        label: t.label,
        onClick: () => {
          s.setSelectedOpeningType(t.slug);
          s.setMode("add-opening");
        },
      }))
    : [{key: "none", label: "No opening types", disabled: true, onClick: () => {}}];
  const equipment: SplitButtonItem[] = [
    ...(s.equipmentTypes.length
      ? typePickerItems(s.equipmentTypes).map((t) => ({
          key: t.key,
          label: t.label,
          title: "Place this type at the cursor",
          onClick: () => {
            s.setSelectedEquipmentType(t.slug);
            s.setMode("add-equipment");
          },
        }))
      : [{key: "none", label: "No equipment types", disabled: true, onClick: () => {}}]),
    {
      key: "insert",
      label: "Insert onto/into cell…",
      separatorBefore: true,
      disabled: !hasCells || s.equipmentTypes.length === 0,
      title: hasCells ? "Seat the picked type on a cell's floor or roof, centred on its footprint" : "Add a cell first",
      onClick: () => {
        // The insert flow builds the picked type; the first when none is picked yet.
        if (!s.selectedEquipmentType && s.equipmentTypes[0]) s.setSelectedEquipmentType(s.equipmentTypes[0].slug);
        s.openInsertMenu(240, 120, null);
      },
    },
  ];
  return (
    <div className={BAR} role="group" aria-label="Place" data-builder-tools="place">
      <CommandButton id={ID.builderAddCell} />
      <CommandButton id={ID.builderAddLoft} />
      <SplitButton
        id={ID.builderAddOpening}
        items={opening}
        chosen={pickedTypeLabel(s.openingTypes, s.selectedOpeningType)}
        menuHeader={<span className="font-medium text-gray-200">Opening type</span>}
      />
      <SplitButton
        id={ID.builderAddEquipment}
        items={equipment}
        chosen={pickedTypeLabel(s.equipmentTypes, s.selectedEquipmentType)}
        menuHeader={<span className="font-medium text-gray-200">Equipment type</span>}
      />
    </div>
  );
}

/** The move, rotate and resize gizmos on the selected component (the G, R and S keys). */
export function BuilderGizmoTools() {
  useBuilderKey();
  return (
    <div className={BAR} role="group" aria-label="Gizmos" data-builder-tools="gizmo">
      <CommandButton id={ID.builderTranslate} />
      <CommandButton id={ID.builderRotate} />
      <CommandButton id={ID.builderResize} />
    </div>
  );
}

/** What the builder shows: the topology, the simulation or the detail model; the result over or beside the topology. */
export function BuilderViewTools() {
  useBuilderKey();
  return (
    <div className={BAR} role="group" aria-label="Model view" data-builder-tools="view">
      <CommandButton id={ID.builderRepTopology}>
        <span className="ml-1">Topology</span>
      </CommandButton>
      <CommandButton id={ID.builderRepSimulation}>
        <span className="ml-1">Sim</span>
      </CommandButton>
      <CommandButton id={ID.builderRepDetail}>
        <span className="ml-1">Detail</span>
      </CommandButton>
      <CommandButton id={ID.builderSuperimpose} />
      <CommandButton id={ID.builderSideBySide} />
    </div>
  );
}

/**
 * The procedural builder: which model is open and whether it has unsaved changes, undo / redo, compile
 * (on the server, forced, in the browser), commit, and closing the model. Each button is a core command,
 * greyed with its reason when the model is read-only, the connection cannot compile, or there is nothing to do.
 */
export function BuilderToolbar({className = BAR}: {className?: string}) {
  useBuilderKey();
  const s = useCellBuilderStore.getState();
  const loaded = s.active !== null || hasEmbeddedDoc(s);
  return (
    <div className={className} role="group" aria-label="Procedural builder" data-mode-toolbar="build">
      <span className="mr-1 max-w-48 truncate font-semibold" data-builder-model="" title={s.active?.modelId ?? undefined}>
        {s.active ? `${s.active.name} r${s.active.revision}` : loaded ? "Model from the scene (read-only)" : "No procedural model"}
      </span>
      {s.dirty && (
        <span className="mr-1 whitespace-nowrap text-amber-400" data-builder-dirty="">
          unsaved
        </span>
      )}
      <CommandButton id={ID.builderNew}>
        {!loaded && <span className="ml-1">New model</span>}
      </CommandButton>
      <CommandButton id={ID.builderUndo} />
      <CommandButton id={ID.builderRedo} />
      {DIVIDER}
      <CommandButton id={ID.builderCompile}>
        <span className="ml-1">{s.compileJob?.status === "queued" || s.compileJob?.status === "running" ? "Compiling…" : "Compile"}</span>
      </CommandButton>
      <CommandButton id={ID.builderCompileBrowser} label="Compile in browser (WASM)" />
      <CommandButton id={ID.builderCommit}>
        <span className="ml-1">{s.committing ? "Committing…" : "Commit"}</span>
      </CommandButton>
      {DIVIDER}
      <CommandButton id={ID.builderClose} />
      {loaded && (
        <>
          {DIVIDER}
          <BuilderPlacementTools />
          {DIVIDER}
          <BuilderGizmoTools />
          {DIVIDER}
          <BuilderViewTools />
        </>
      )}
    </div>
  );
}

/** Core's mode toolbars by id: what `UiShellMode.toolbars` names. */
export const CORE_MODE_TOOLBARS: Readonly<Record<CoreModeToolbarId, React.FC>> = {
  build: () => <BuilderToolbar />,
  playback: () => <PlaybackToolbar />,
  section: () => <SectionToolbar />,
  representation: () => <RepresentationToolbar />,
  colorBy: () => <ColorByToolbar />,
};

/** The core toolbars named by `ids`, in that order, a divider between; an unknown id is skipped. */
export function CoreModeToolbars({ids}: {ids: readonly string[]}) {
  const known = ids.filter((id): id is CoreModeToolbarId => (CORE_MODE_TOOLBAR_IDS as readonly string[]).includes(id));
  if (!known.length) return null;
  return (
    <div className={BAR} data-mode-toolbars="">
      {known.map((id, i) => {
        const Toolbar = CORE_MODE_TOOLBARS[id];
        return (
          <React.Fragment key={id}>
            {i > 0 && DIVIDER}
            <Toolbar />
          </React.Fragment>
        );
      })}
    </div>
  );
}
