// The console of the run whose result is on screen: what each of its actors is doing and planning
// at the moment the playback clock shows, and everything that was said up to then. Fed by the run's
// activity feed (state/runConsoleStore.ts), whoever the provider is.
//
// Over the view on either viewport, beside the playback transport, whose "Console" button turns it
// on and off. With the clock paused on a moment it is that moment's console; with a live run it
// moves with the view. A line of the log is a moment: a click on it goes there.
// No three.js in this module.

import React, { useEffect, useRef, useSyncExternalStore } from "react";

import { clockText } from "@/components/playback/clockText";
import type { ActivityActor, ActivityEntry, ActivityFeed } from "@/services/simulations/activityFeed";
import { playbackClock } from "@/state/playbackClock";
import { useRunConsoleStore } from "@/state/runConsoleStore";

/** Most lines of the log drawn at once: the latest up to the clock's time. */
export const CONSOLE_LINES = 200;

const LEVEL_CLASS: Record<string, string> = { info: "text-gray-100", warn: "text-yellow-300", error: "text-red-300" };
const KIND_MARK: Record<string, string> = { state: "▸", plan: "→", event: "·" };

const getClock = () => playbackClock.state();

/** What the console shows of `feed` at `time`: each actor's state and plan, and the lines up to then. */
export function consoleAt(
  feed: ActivityFeed,
  time: number,
  lines = CONSOLE_LINES,
): { actors: { actor: ActivityActor; state?: ActivityEntry; plan?: ActivityEntry }[]; log: ActivityEntry[]; earlier: number } {
  const count = feed.countUpTo(time);
  const from = Math.max(0, count - lines);
  return {
    actors: feed.actors.map((actor) => ({ actor, state: feed.stateAt(actor.id, time), plan: feed.planAt(actor.id, time) })),
    log: feed.entries.slice(from, count),
    earlier: from,
  };
}

const ActorRow: React.FC<{ actor: ActivityActor; state?: ActivityEntry; plan?: ActivityEntry; time: number }> = ({ actor, state, plan, time }) => (
  <li className="flex flex-col gap-0.5 rounded-sm bg-gray-800/60 px-1.5 py-1" data-console-actor={actor.id}>
    <div className="flex items-baseline gap-1.5 min-w-0">
      <span className="h-2 w-2 shrink-0 rounded-full self-center" style={{ background: actor.color || "#9ca3af" }} aria-hidden />
      <span className="shrink-0 font-medium" style={actor.color ? { color: actor.color } : undefined}>
        {actor.label}
      </span>
      <span className={`min-w-0 flex-1 break-words ${state ? LEVEL_CLASS[state.level] : "text-gray-500"}`} data-console-state>
        {state ? state.text : "—"}
      </span>
      {state && (
        <span className="shrink-0 tabular-nums text-gray-500" title="For how long">
          {clockText(Math.max(0, time - state.t))}
        </span>
      )}
    </div>
    {plan && (
      <div className="pl-3.5 text-gray-400 break-words" data-console-plan title="What it intends to do">
        → {plan.text}
      </div>
    )}
  </li>
);

const RunConsole: React.FC = () => {
  const clock = useSyncExternalStore(playbackClock.subscribe, getClock, getClock);
  const run = useRunConsoleStore((s) => s.run);
  const feed = useRunConsoleStore((s) => s.feed);
  // Read so that more of the same feed draws again.
  useRunConsoleStore((s) => s.version);
  const shown = useRunConsoleStore((s) => s.shown);
  const live = useRunConsoleStore((s) => s.live);
  const error = useRunConsoleStore((s) => s.error);
  const setShown = useRunConsoleStore((s) => s.setShown);
  const close = useRunConsoleStore((s) => s.close);
  const logRef = useRef<HTMLOListElement>(null);
  /** Whether the log is scrolled to its end: it then stays there as lines are added. */
  const atEnd = useRef(true);

  // The result left the screen (nothing timed is on it): the console is of nothing.
  const tracks = clock.tracks;
  const hadTracks = useRef(false);
  useEffect(() => {
    if (tracks > 0) hadTracks.current = true;
    else if (hadTracks.current && run) {
      hadTracks.current = false;
      close();
    }
  }, [tracks, run, close]);

  const view = feed && shown ? consoleAt(feed, clock.time) : null;
  const lines = view?.log.length ?? 0;
  const last = view?.log[lines - 1];
  useEffect(() => {
    const el = logRef.current;
    if (el && atEnd.current) el.scrollTop = el.scrollHeight;
  }, [lines, last]);

  if (!run || !shown || tracks === 0) return null;
  const colorOf = new Map(feed?.actors.map((a) => [a.id, a] as const));
  return (
    <div
      className="absolute right-3 top-14 z-10 flex max-h-[62%] w-[min(92%,440px)] flex-col gap-1.5 rounded-md bg-gray-900/85 p-2 text-xs text-white"
      data-run-console="true"
      // A press on the console is not a press on the model under it.
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
    >
      <div className="flex items-center gap-2">
        <span className="font-semibold">Console</span>
        <span className="min-w-0 flex-1 truncate text-gray-300" title={run.title}>
          {run.title}
        </span>
        {live && <span className="shrink-0 text-blue-300">running</span>}
        <span className="shrink-0 tabular-nums text-gray-400">{clockText(clock.time)}</span>
        <button
          type="button"
          className="shrink-0 rounded-sm px-1 text-gray-300 hover:bg-gray-700 hover:text-white"
          title="Hide the console"
          aria-label="Hide the console"
          onClick={() => setShown(false)}
        >
          ✕
        </button>
      </div>
      {error && <div className="break-words text-red-300">{error}</div>}
      {!feed && !error && (
        <div className="text-gray-400">{live ? "Waiting for the run to say what it is doing…" : "This run says nothing about what it did."}</div>
      )}
      {view && (
        <>
          <ul className="flex shrink-0 flex-col gap-0.5">
            {view.actors.map(({ actor, state, plan }) => (
              <ActorRow key={actor.id} actor={actor} state={state} plan={plan} time={clock.time} />
            ))}
          </ul>
          <ol
            ref={logRef}
            className="flex min-h-12 flex-col overflow-y-auto overscroll-contain rounded-sm bg-black/30 px-1 py-0.5 font-mono"
            data-console-log
            onScroll={(e) => {
              const el = e.currentTarget;
              atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < 8;
            }}
          >
            {view.earlier > 0 && <li className="text-gray-500">… {view.earlier} earlier</li>}
            {view.log.map((entry, i) => {
              const actor = colorOf.get(entry.actor);
              return (
                <li key={view.earlier + i}>
                  <button
                    type="button"
                    className="flex w-full items-baseline gap-1.5 text-left hover:bg-gray-700/60"
                    title="Go to this moment"
                    onClick={() => playbackClock.seek(entry.t)}
                  >
                    <span className="shrink-0 tabular-nums text-gray-500">{clockText(entry.t)}</span>
                    <span className="shrink-0" style={actor?.color ? { color: actor.color } : undefined}>
                      {actor?.label ?? entry.actor}
                    </span>
                    <span className="shrink-0 text-gray-500" aria-hidden>
                      {KIND_MARK[entry.kind]}
                    </span>
                    <span className={`min-w-0 flex-1 break-words ${LEVEL_CLASS[entry.level]}`}>{entry.text || "(no plan)"}</span>
                  </button>
                </li>
              );
            })}
            {view.log.length === 0 && <li className="text-gray-500">Nothing yet at this moment.</li>}
          </ol>
        </>
      )}
    </div>
  );
};

export default RunConsole;
