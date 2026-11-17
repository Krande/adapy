// The transport of the playback clock (state/playbackClock.ts): play and pause, a scrub bar, the
// time, the speed, loop, and "live" for a recording that is still growing. Drawn over the bottom of
// the view on either viewport, and only while a track is registered: with nothing timed on screen
// it renders nothing. The FEA result playback has its own controls in the Simulation panel.
// The console of the run on screen is a panel of the layout (`core:run-console`): the transport
// has the button that opens it, and ends the console when the result leaves the screen.
// No three.js in this module.

import React, { useEffect, useRef, useSyncExternalStore } from "react";

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
import { useRunConsoleStore } from "@/state/runConsoleStore";

import { clockText } from "./clockText";

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

export { clockText };

const getState = () => playbackClock.state();

const PlaybackTransport: React.FC = () => {
  const state = useSyncExternalStore(playbackClock.subscribe, getState, getState);
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
      {spec.icon === "stop" ? <StopIcon /> : spec.label}
    </button>
  );
  const speeds = SPEEDS.includes(state.speed) ? SPEEDS : [...SPEEDS, state.speed].sort((a, b) => a - b);
  return (
    <div
      // One row where it fits. On a phone the scrub bar takes a row of its own (`order` and
      // `basis-full`) and the buttons wrap under it, so none of them is squeezed off the bar.
      className="absolute bottom-3 left-1/2 z-10 flex w-[min(92%,640px)] -translate-x-1/2 flex-wrap items-center gap-2 rounded-md bg-gray-900/80 px-2 py-1 text-xs text-white"
      data-playback-transport="true"
      data-playing={state.playing ? "true" : "false"}
      // A press on the transport is not a press on the model under it.
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className={button()}
        title={state.playing ? "Pause" : "Play"}
        aria-label={state.playing ? "Pause" : "Play"}
        onClick={() => playbackClock.toggle()}
      >
        {state.playing ? "❚❚" : "▶"}
      </button>
      {actions.filter((a) => a.spec.beside === "play").map(actionButton)}
      <input
        type="range"
        className="order-first h-8 min-w-0 flex-1 basis-full sm:order-none sm:h-auto sm:basis-0"
        aria-label="Time"
        min={0}
        max={state.duration || 1}
        step="any"
        value={Math.min(state.time, state.duration)}
        onChange={(e) => playbackClock.seek(Number(e.target.value))}
      />
      <span className="shrink-0 tabular-nums" data-playback-time="true">
        {clockText(state.time)} / {clockText(state.duration)}
      </span>
      <select
        className="shrink-0 rounded-sm bg-white px-1 py-0.5 text-black"
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
