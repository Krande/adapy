/**
 * The engine COMPILE LOG readout.
 *
 * Owns: the messages the engine emitted during the last build, including whether
 * the log belongs to this run or to the run that originally built a cached
 * artifact -- as a view of its own (`CompileLogView`, which a shell shows where it
 * likes, a drawer that opens when a compile fails, say) and as the Tools tab's
 * section.
 */

import React from "react";

import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {Section, btnGray} from "./chrome";

/** The log, its Copy button and where it came from. `maxHeightClass` bounds the log's height
 * (default `max-h-56`; `max-h-none` for a host that scrolls it itself). */
export const CompileLogView: React.FC<{maxHeightClass?: string}> = ({maxHeightClass = "max-h-56"}) => {
  const log = useCellBuilderStore((st) => st.compileLog);
  const runId = useCellBuilderStore((st) => st.compileLogRunId);
  const isCurrentRun = useCellBuilderStore((st) => st.compileLogIsCurrentRun);
  const [copied, setCopied] = React.useState(false);
  const onCopy = () => {
    if (!log) return;
    void navigator.clipboard?.writeText(log).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      },
      () => {},
    );
  };
  return (
    <div className="flex flex-col gap-1" data-compile-log="">
      {log ? (
        <>
          <div className="flex items-center gap-2">
            <button
              className={btnGray}
              onClick={onCopy}
              title="Copy the engine log to the clipboard"
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <span
              className="text-gray-500 text-[11px] truncate"
              title={runId ? `compile run ${runId}` : undefined}
            >
              {runId
                ? isCurrentRun
                  ? `engine messages · run ${runId.slice(0, 8)}`
                  : `from an earlier run (${runId.slice(0, 8)}) — this result was cached`
                : "engine messages from the last compile"}
            </span>
          </div>
          <pre className={`${maxHeightClass} overflow-auto whitespace-pre-wrap break-words bg-black/40 border border-gray-700 rounded-sm p-1.5 text-[11px] font-mono text-gray-200`}>
            {log.trim() ? log : "(engine emitted no messages)"}
          </pre>
        </>
      ) : (
        <p className="text-gray-500 text-[12px]">
          No log yet — compile to see engine messages.
        </p>
      )}
    </div>
  );
};

export const CompileLogSection: React.FC = () => (
  <Section title="Compile log">
    <CompileLogView />
  </Section>
);
