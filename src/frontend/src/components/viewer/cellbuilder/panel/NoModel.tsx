/**
 * What the builder panel shows while no procedural model is open: the way in. A shell that docks the
 * panel in a mode of its own (Build) has it on screen with nothing loaded, so the panel starts from here.
 */

import React from "react";

import {runCommand} from "@/layout/commands";
import {CORE_COMMAND_IDS} from "@/layout/coreCommandIds";
import {PanelHeaderActions} from "@/components/ui/PanelFrame";
import {btn} from "./chrome";

export const NoModel: React.FC = () => (
  <div className="flex flex-col gap-2 p-2.5" data-builder-no-model="">
    <div className="flex items-center gap-2">
      <span className="font-semibold">No procedural model is open</span>
      <span className="ml-auto" />
      <PanelHeaderActions />
    </div>
    <p className="text-gray-400">Start a model here, or open a saved one from the Storage panel (its + menu starts one from a template).</p>
    <button type="button" className={`${btn} self-start`} onClick={() => void runCommand(CORE_COMMAND_IDS.builderNew)}>
      New procedural model…
    </button>
  </div>
);
