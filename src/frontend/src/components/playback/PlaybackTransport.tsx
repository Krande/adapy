// The transport of the playback clock (state/playbackClock.ts): play and pause, steps back and on
// by a chosen size, a scrub bar that scrubs finely and zooms (./ScrubBar.tsx) with the markers
// sources put on it, the time to the millisecond (click it to type one), the speed, loop, "live"
// for a recording that is still growing, and "capture frame". Drawn over the bottom of the view on
// either viewport, and only while a track is registered: with nothing timed on screen it renders
// nothing. The FEA result playback has its own controls in the Simulation panel.
// The console of the run on screen is a panel of the layout (`core:run-console`): the transport
// has the button that opens it, and ends the console when the result leaves the screen.
// Its keys (← / → step, Shift ×10, Alt ÷10, [ / ] markers) are ./playbackKeys.ts.
// No three.js in this module.

import React, { useEffect, useRef, useState, useSyncExternalStore } from "react";

import CameraIcon from "@/components/icons/CameraIcon";
import StopIcon from "@/components/icons/StopIcon";
import { CORE_PANEL_IDS } from "@/layout/corePanelIds";
import {
  listPlaybackActions,
  playbackActionState,
  playbackActionsVersion,
  subscribePlaybackActions,
} from "@/layout/playbackActions";
import { isPanelOpen, toggle } from "@/layout/reveal";
import { usePluginUiStore } from "@/plugins/pluginUiStore";
import { playbackClock } from "@/state/playbackClock";
import { followTime, isZoomed, parseTimeInput, preciseClockText, stepSizeLabel, STEP_SIZES } from "@/state/playbackTimeline";
import { useRunConsoleStore } from "@/state/runConsoleStore";

import { clockText } from "./clockText";
import { captureFrame } from "./frameCapture";
import { installPlaybackKeys, type PlaybackKeyAction } from "./playbackKeys";
import ScrubBar from "./ScrubBar";
import { timelineView } from "./timelineView";

const SPEEDS = [0.1, 0.25, 0.5, 1, 2, 4, 8];
// A finger's size at phone width, a mouse's from `sm` up: the same convention as the panel
// headers' buttons (components/ui/panelChrome.ts).
const BUTTON_BASE =
  "h-11 min-w-11 px-2 text-sm sm:h-7 sm:min-w-8 sm:px-1 sm:text-xs " +
  "shrink-0 grid place-items-center rounded-md text-white transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300";
// A toggle that is on is the darker blue the top bar's buttons are when their panel is open
// (components/navButton.ts), not a ring around the same blue.
const BUTTON_OFF = "bg-blue-700 hover:bg-blue-600";
const BUTTON_ON = "bg-blue-900 hover:bg-blue-800 shadow-inner";
const button = (on = false): string => `${BUTTON_BASE} ${on ? BUTTON_ON : BUTTON_OFF}`;
const SELECT = "shrink-0 rounded-sm bg-white px-1 py-0.5 text-black";

export { clockText };

const getState = () => playbackClock.state();

/** Do what one of the transport's keys asks. */
export function runPlaybackKey(action: PlaybackKeyAction): void {
  if (action.kind === "marker") playbackClock.seekMarker(action.direction);
  else playbackClock.step(action.direction, timelineView.get().stepSize, { shift: action.shift, alt: action.alt });
}

/** The time, to the millisecond; a click turns it into a field to type a time to go to. */
const TimeReadout: React.FC<{ time: number; duration: number }> = ({ time, duration }) => {
  const [editing, setEditing] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  if (editing !== null) {
    const go = () => {
      const t = parseTimeInput(editing, time);
      if (t === null) return setBad(true);
      playbackClock.pause();
      playbackClock.seek(t);
      setEditing(null);
      setBad(false);
    };
    return (
      <input
        type="text"
        autoFocus
        className={`w-28 shrink-0 rounded-sm px-1 py-0.5 font-mono tabular-nums text-black ${bad ? "bg-red-100 ring-2 ring-red-400" : "bg-white"}`}
        aria-label="Go to time"
        title="A time to go to: 1:23.456, 83.456, 1500ms; +0.1 or -250ms from now. Enter goes, Escape cancels"
        value={editing}
        data-playback-time-input="true"
        onChange={(e) => {
          setEditing(e.target.value);
          setBad(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") go();
          else if (e.key === "Escape") setEditing(null);
          e.stopPropagation();
        }}
        onBlur={() => setEditing(null)}
      />
    );
  }
  return (
    <button
      type="button"
      className="shrink-0 rounded-sm px-1 font-mono tabular-nums hover:bg-white/10"
      title="Click to type a time to go to"
      data-playback-time="true"
      onClick={() => setEditing(preciseClockText(time))}
    >
      {preciseClockText(time)} / {preciseClockText(duration)}
    </button>
  );
};

const PlaybackTransport: React.FC = () => {
  const state = useSyncExternalStore(playbackClock.subscribe, getState, getState);
  const view = useSyncExternalStore(timelineView.subscribe, timelineView.get, timelineView.get);
  // The console of the run whose result is on screen (state/runConsoleStore.ts): offered while
  // such a run is open, whether or not it has said anything yet. It is a panel of the layout,
  // opened and closed through it; its flag is read so the button follows a close from elsewhere.
  const consoleRun = useRunConsoleStore((s) => s.run);
  useRunConsoleStore((s) => s.shown);
  const consoleShown = isPanelOpen(CORE_PANEL_IDS.runConsole);
  // The result left the screen (nothing timed is on it): the console is of nothing. Here, not in
  // the console: the transport is mounted whether the console is open, closed or docked.
  const closeConsole = useRunConsoleStore((s) => s.close);
  const tracks = state.tracks;
  const hadTracks = useRef(false);
  useEffect(() => {
    if (tracks > 0) hadTracks.current = true;
    else if (hadTracks.current && consoleRun) {
      hadTracks.current = false;
      closeConsole();
    }
  }, [tracks, consoleRun, closeConsole]);
  // Nothing timed: the next timeline starts unzoomed.
  useEffect(() => {
    if (tracks === 0) timelineView.resetZoom();
  }, [tracks]);
  // The keys, while something is timed on screen.
  useEffect(() => installPlaybackKeys(() => playbackClock.state().tracks > 0, runPlaybackKey), []);
  // Zoomed in, the window pages on as the time leaves it (playing, a step, a marker).
  const shown = timelineView.window(state.duration);
  useEffect(() => {
    if (!view.zoom) return;
    const next = followTime(shown, state.time, state.duration);
    if (next !== shown) timelineView.setZoom(next, state.duration);
  }, [state.time, state.duration, view.zoom]); // `shown` is made of these
  const [capturing, setCapturing] = useState(false);
  // Buttons a shell or a plugin added (layout/playbackActions.ts): asked again when the list
  // changes, when one says so, and when a plugin panel opens or closes -- which is what most of
  // them toggle, and what the panel's own close button changes behind their back.
  useSyncExternalStore(subscribePlaybackActions, playbackActionsVersion, playbackActionsVersion);
  usePluginUiStore((s) => s.visible);
  const [, pressedAgain] = React.useReducer((n: number) => n + 1, 0);
  if (state.tracks === 0) return null;
  const actions = listPlaybackActions()
    .map((spec) => ({ spec, shown: playbackActionState(spec) }))
    .filter((a) => a.shown !== null);
  const actionButton = ({ spec, shown }: (typeof actions)[number]) => (
    <button
      key={spec.id}
      type="button"
      className={button(!!shown!.pressed)}
      title={spec.title ?? spec.label}
      aria-label={spec.label}
      aria-pressed={shown!.pressed}
      data-playback-action={spec.id}
      onClick={() => {
        try {
          spec.run();
        } catch (err) {
          console.error(`[playback] action "${spec.id}" threw`, err);
        }
        pressedAgain();
      }}
    >
      {spec.icon === "stop" ? <StopIcon /> : spec.icon === "camera" ? <CameraIcon /> : spec.label}
    </button>
  );
  const speeds = SPEEDS.includes(state.speed) ? SPEEDS : [...SPEEDS, state.speed].sort((a, b) => a - b);
  const markers = playbackClock.markers();
  const zoomed = isZoomed(shown, state.duration);
  const sizeText = stepSizeLabel(view.stepSize);
  const stepTitle = (dir: string, key: string) => `${dir} ${sizeText} (${key}; Shift ×10, Alt ÷10)`;
  const step = (direction: 1 | -1) => (e: React.MouseEvent) =>
    playbackClock.step(direction, view.stepSize, { shift: e.shiftKey, alt: e.altKey });
  return (
    <div
      // The scrub bar has a row of its own and the buttons wrap under it, so none of them is
      // squeezed off the bar.
      className="absolute bottom-3 left-1/2 z-10 flex w-[min(96%,780px)] -translate-x-1/2 flex-wrap items-center gap-x-2 gap-y-1 rounded-md bg-gray-900/80 px-2 py-1 text-xs text-white"
      data-playback-transport="true"
      data-playing={state.playing ? "true" : "false"}
      // A press on the transport is not a press on the model under it.
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
    >
      <div className="flex w-full basis-full items-center gap-2">
        <ScrubBar
          time={state.time}
          duration={state.duration}
          view={shown}
          markers={markers}
          onSeek={(t) => playbackClock.seek(t)}
          onZoom={(w) => timelineView.setZoom(w, state.duration)}
        />
        {zoomed && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-gray-300" data-playback-zoom-window="true">
            {preciseClockText(shown.start)}–{preciseClockText(shown.end)}
          </span>
        )}
        {zoomed && (
          <button
            type="button"
            className={button()}
            title="Show the whole timeline again"
            aria-label="Reset zoom"
            data-playback-zoom-reset="true"
            onClick={() => timelineView.resetZoom()}
          >
            ⤢
          </button>
        )}
      </div>
      <button
        type="button"
        className={button()}
        title={stepTitle("Back", "←")}
        aria-label="Step back"
        data-playback-step="-1"
        onClick={step(-1)}
      >
        |◀
      </button>
      <button
        type="button"
        className={button()}
        title={state.playing ? "Pause" : "Play"}
        aria-label={state.playing ? "Pause" : "Play"}
        onClick={() => playbackClock.toggle()}
      >
        {state.playing ? "❚❚" : "▶"}
      </button>
      <button
        type="button"
        className={button()}
        title={stepTitle("On", "→")}
        aria-label="Step forward"
        data-playback-step="1"
        onClick={step(1)}
      >
        ▶|
      </button>
      {actions.filter((a) => a.spec.beside === "play").map(actionButton)}
      <select
        className={SELECT}
        aria-label="Step size"
        title="How far a step goes: one recorded sample (a display frame where there are none), or a time"
        value={String(view.stepSize)}
        data-playback-step-size="true"
        onChange={(e) => timelineView.setStepSize(e.target.value === "frame" ? "frame" : Number(e.target.value))}
      >
        {STEP_SIZES.map((size) => (
          <option key={String(size)} value={String(size)}>
            {stepSizeLabel(size)}
          </option>
        ))}
      </select>
      <TimeReadout time={state.time} duration={state.duration} />
      {markers.length > 0 && (
        <>
          <button
            type="button"
            className={button()}
            title="Previous marker ([)"
            aria-label="Previous marker"
            data-playback-marker-step="-1"
            onClick={() => playbackClock.seekMarker(-1)}
          >
            ◆◀
          </button>
          <button
            type="button"
            className={button()}
            title="Next marker (])"
            aria-label="Next marker"
            data-playback-marker-step="1"
            onClick={() => playbackClock.seekMarker(1)}
          >
            ▶◆
          </button>
        </>
      )}
      <select
        className={SELECT}
        aria-label="Speed"
        title="Speed"
        value={state.speed}
        onChange={(e) => playbackClock.setSpeed(Number(e.target.value))}
      >
        {speeds.map((speed) => (
          <option key={speed} value={speed}>
            {speed}×
          </option>
        ))}
      </select>
      <button
        type="button"
        className={button(state.loop)}
        title="Start again at the end"
        aria-label="Loop"
        aria-pressed={state.loop}
        onClick={() => playbackClock.setLoop(!state.loop)}
      >
        ↻
      </button>
      <button
        type="button"
        className={button(state.followEnd)}
        title="Stay at the end while the recording grows"
        aria-label="Follow the end"
        aria-pressed={state.followEnd}
        onClick={() => playbackClock.setFollowEnd(!state.followEnd)}
      >
        Live
      </button>
      <button
        type="button"
        className={button(capturing)}
        title={`Capture this frame: pause and save the view at ${preciseClockText(state.time)}`}
        aria-label="Capture frame"
        data-playback-capture="true"
        disabled={capturing}
        onClick={() => {
          setCapturing(true);
          void captureFrame()
            .catch((err) => console.error("[playback] capture failed", err))
            .finally(() => setCapturing(false));
        }}
      >
        <CameraIcon />
      </button>
      {consoleRun && (
        <button
          type="button"
          className={button(consoleShown)}
          title="What each actor of the run is doing and planning at this moment"
          aria-label="Console"
          aria-pressed={consoleShown}
          data-run-console-toggle
          onClick={() => void toggle(CORE_PANEL_IDS.runConsole)}
        >
          Console
        </button>
      )}
      {actions.filter((a) => a.spec.beside !== "play").map(actionButton)}
    </div>
  );
};

export default PlaybackTransport;
