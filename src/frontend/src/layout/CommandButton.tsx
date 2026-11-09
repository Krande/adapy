// A toolbar button over a registered command (`./commands`): the building block of core's mode
// toolbars (`./ModeToolbars`) and of a shell's own, with `SplitButton` (`./SplitButton`) for an
// action that also has a picker.

import React from "react";

import {CORE_COMMAND_ICONS} from "./commandIcons";
import {commandsVersion, getCommand, runCommand, subscribeCommands} from "./commands";

/** The button's look: a compact icon button, filled while the command is checked. */
export const COMMAND_BUTTON =
  "inline-flex h-6 min-w-6 items-center justify-center rounded-sm px-1 transition-colors disabled:opacity-40 " +
  "hover:enabled:bg-white/10";
export const COMMAND_BUTTON_ON = "bg-[var(--ada-tab-active-bg,#374151)] text-[var(--ada-tab-active-text,#ffffff)]";

/** Re-render when a command is registered or replaced. */
export function useCommandsVersion(): void {
  React.useSyncExternalStore(subscribeCommands, commandsVersion, commandsVersion);
}

export interface CommandButtonProps {
  /** A registered command's id. */
  id: string;
  /** Overrides the command's label as the tooltip and the accessible name. */
  label?: string;
  /** Overrides the command's icon (a toggle that swaps icons). */
  icon?: React.FC;
  /** Overrides the command's ticked state. */
  pressed?: boolean;
  /** Runs in place of the command when pressed (a split button's "pick a type first"); the command still decides whether it is offered and enabled. */
  onPress?: () => void;
  /** Extra classes (a split button squares the corners it shares). */
  className?: string;
  /** Text after the icon. */
  children?: React.ReactNode;
}

/**
 * A button that runs a command: its icon, label and tooltip from the command,
 * greyed (with the reason as the tooltip) while it cannot run, pressed while it
 * is checked. Nothing when the command is not offered. Its state is read at
 * render; the caller re-renders it when the state it shows changes.
 */
export function CommandButton({id, label, icon, pressed, onPress, className, children}: CommandButtonProps) {
  useCommandsVersion();
  const c = getCommand(id);
  if (!c || (c.when && !c.when())) return null;
  const reason = c.disabledReason?.() ?? null;
  const checked = pressed ?? c.checked?.();
  const Icon = icon ?? c.icon ?? CORE_COMMAND_ICONS[id];
  const name = label ?? c.label;
  return (
    <button
      type="button"
      className={`${COMMAND_BUTTON} ${checked ? COMMAND_BUTTON_ON : ""} ${className ?? ""}`}
      title={reason ? `${name} (${reason})` : name}
      aria-label={name}
      aria-pressed={checked === undefined ? undefined : checked}
      disabled={!!reason}
      data-command={id}
      onClick={() => (onPress ? onPress() : void runCommand(id))}
    >
      {Icon && <Icon />}
      {children}
    </button>
  );
}
