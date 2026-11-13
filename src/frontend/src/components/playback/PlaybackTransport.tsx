// The transport of the playback clock (state/playbackClock.ts): play and pause, a scrub bar, the
// time, the speed, loop, and "live" for a recording that is still growing. Drawn over the bottom of
// the view on either viewport, and only while a track is registered: with nothing timed on screen
// it renders nothing. The FEA result playback has its own controls in the Simulation panel.
// No three.js in this module.

import React, { useSyncExternalStore } from "react";

import RunConsole from "@/components/simulations/RunConsole";
import { playbackClock } from "@/state/playbackClock";
import { useRunConsoleStore } from "@/state/runConsoleStore";

import { clockText } from "./clockText";

const SPEEDS = [0.1, 0.25, 0.5, 1, 2, 4, 8];
const BUTTON =
  "h-7 min-w-8 px-1 shrink-0 grid place-items-center rounded-md bg-blue-700 text-white hover:bg-blue-600 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300";
const TOGGLE_ON = "ring-2 ring-blue-300";

export { clockText };

const getState = () => playbackClock.state();

const PlaybackTransport: React.FC = () => {
  const state = useSyncExternalStore(playbackClock.subscribe, getState, getState);
  // The console of the run whose result is on screen (state/runConsoleStore.ts): offered while
  // such a run is open, whether or not it has said anything yet.
  const consoleRun = useRunConsoleStore((s) => s.run);
  const consoleShown = useRunConsoleStore((s) => s.shown);
  const setConsoleShown = useRunConsoleStore((s) => s.setShown);
  if (state.tracks === 0) return <RunConsole />;
  const speeds = SPEEDS.includes(state.speed) ? SPEEDS : [...SPEEDS, state.speed].sort((a, b) => a - b);
  return (
    <>
    <RunConsole />
    <div
      className="absolute bottom-3 left-1/2 z-10 flex w-[min(92%,640px)] -translate-x-1/2 items-center gap-2 rounded-md bg-gray-900/80 px-2 py-1 text-xs text-white"
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
        className={BUTTON}
        title={state.playing ? "Pause" : "Play"}
        aria-label={state.playing ? "Pause" : "Play"}
        onClick={() => playbackClock.toggle()}
      >
        {state.playing ? "❚❚" : "▶"}
      </button>
      <input
        type="range"
        className="min-w-0 flex-1"
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
        className={`${BUTTON} ${state.loop ? TOGGLE_ON : ""}`}
        title="Start again at the end"
        aria-label="Loop"
        aria-pressed={state.loop}
        onClick={() => playbackClock.setLoop(!state.loop)}
      >
        ↻
      </button>
      <button
        type="button"
        className={`${BUTTON} ${state.followEnd ? TOGGLE_ON : ""}`}
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
          className={`${BUTTON} ${consoleShown ? TOGGLE_ON : ""}`}
          title="What each actor of the run is doing and planning at this moment"
          aria-label="Console"
          aria-pressed={consoleShown}
          data-run-console-toggle
          onClick={() => setConsoleShown(!consoleShown)}
        >
          Console
        </button>
      )}
    </div>
    </>
  );
};

export default PlaybackTransport;
