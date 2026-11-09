// A split button for toolbars: an action on the main half and a picker on a caret.
//
// The main half is a `CommandButton` (its icon, tooltip, pressed and greyed state come from the
// command); the caret opens a `PositionedMenu` of choices. The classic use is "add a door" /
// "add equipment": the caret picks the type, the main half places the type picked last, so
// placing the tenth identical door costs no menu -- and while nothing is picked yet, the main half
// opens the picker instead of arming a tool with nothing to place.

import React from "react";

import {PositionedMenu, type KebabMenuItem} from "@/components/common/PositionedMenu";

import {CommandButton, COMMAND_BUTTON, useCommandsVersion} from "./CommandButton";
import {getCommand} from "./commands";

/** One choice in the picker. */
export type SplitButtonItem = KebabMenuItem;

export interface SplitButtonState {
  /** "run" does the action; "pick" opens the picker instead. */
  action: "run" | "pick";
  tooltip: string;
}

/**
 * What pressing the main half does, given the picker and what it has chosen.
 *
 *   * No picker (`hasMenu` false), or `chosen` undefined (the action takes no choice): run.
 *   * Armed already (`pressed`): run -- a second press puts the tool away; asking which type to
 *     cancel would be answering a question nobody asked.
 *   * Something chosen: run it, naming the choice in the tooltip.
 *   * `chosen` null (a choice is needed and none is made): open the picker.
 */
export function splitButtonState(o: {
  label: string;
  hasMenu: boolean;
  chosen?: string | null;
  pressed?: boolean;
  /** What the picker picks, for the tooltip ("type", "format"). */
  noun?: string;
}): SplitButtonState {
  const {label, hasMenu, chosen, pressed, noun = "type"} = o;
  if (!hasMenu || chosen === undefined) return {action: "run", tooltip: label};
  if (chosen) return {action: "run", tooltip: `${label}: ${chosen}`};
  if (pressed) return {action: "run", tooltip: label};
  return {action: "pick", tooltip: `${label} — choose a ${noun}`};
}

export interface SplitButtonProps {
  /** The command the main half runs. */
  id: string;
  /** The picker's choices; empty: an ordinary button. */
  items: readonly SplitButtonItem[];
  /** The label of what the picker has chosen; null: nothing yet (the main half opens the picker); absent: the action takes no choice. */
  chosen?: string | null;
  /** What the picker picks, for the tooltips. Default "type". */
  noun?: string;
  /** The picker's heading. */
  menuHeader?: React.ReactNode;
  /** Overrides the command's label, icon and pressed state, as on `CommandButton`. */
  label?: string;
  icon?: React.FC;
  pressed?: boolean;
  children?: React.ReactNode;
}

/** An action and a picker on a caret, over a core (or the shell's own) command. */
export function SplitButton({id, items, chosen, noun, menuHeader, label, icon, pressed, children}: SplitButtonProps) {
  useCommandsVersion();
  const box = React.useRef<HTMLSpanElement>(null);
  const [open, setOpen] = React.useState(false);
  const c = getCommand(id);
  if (!c || (c.when && !c.when())) return null;
  const reason = c.disabledReason?.() ?? null;
  const name = label ?? c.label;
  const isPressed = pressed ?? c.checked?.();
  const state = splitButtonState({label: name, hasMenu: items.length > 0, chosen, pressed: isPressed, noun});
  return (
    <span ref={box} className="inline-flex items-center" data-split-button={id}>
      <CommandButton
        id={id}
        label={state.tooltip}
        icon={icon}
        pressed={pressed}
        className={items.length ? "rounded-r-none" : ""}
        onPress={state.action === "pick" ? () => setOpen(true) : undefined}
      >
        {children}
      </CommandButton>
      {items.length > 0 && (
        <button
          type="button"
          className={`${COMMAND_BUTTON} min-w-4 rounded-l-none border-l border-white/10 px-0.5 text-[9px]`}
          aria-label={`${name}: choose a ${noun ?? "type"}`}
          title={reason ? `${name} (${reason})` : `Choose a ${noun ?? "type"}`}
          aria-haspopup="menu"
          aria-expanded={open}
          disabled={!!reason}
          data-split-caret={id}
          onClick={() => setOpen((v) => !v)}
        >
          <span aria-hidden="true">▾</span>
        </button>
      )}
      {open && (
        <PositionedMenu
          anchor={{kind: "rect", getRect: () => box.current?.getBoundingClientRect()}}
          ignoreOutsideRef={box}
          onClose={() => setOpen(false)}
          header={menuHeader}
          items={items.map((it) => ({
            ...it,
            onClick: () => {
              setOpen(false);
              it.onClick();
            },
          }))}
        />
      )}
    </span>
  );
}
