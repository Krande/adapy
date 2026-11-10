// The Gamepad API as a 3D-mouse source: polls the browser's pad list, writes the
// cap's axes into the same `AxisState` the WebHID path fills, and keeps a low-rate
// idle poll running so an on-demand render loop wakes when the cap moves.
//
// Everything the browser provides comes in through `GamepadSourceOptions`, so the
// whole thing runs under `node --test` with a fake `getGamepads`.
//
// SOURCE ORDER is decided by the caller (./spaceMouseDevice), through `setSuspended`:
// an opened WebHID device (the desktop app's native reader included) drives the axes,
// and this source stands by -- no polling, no idle timer -- until it goes away.

import {type AxisState, resetAxes, takePressedButtons} from "./hidReport";
import {padKey, padMapFor, type PadLike, readPad, selectPad} from "./gamepadInput";

export type PadStatus =
    | {kind: "none"}
    | {kind: "active"; name: string; vendorId: number; productId: number}
    /** Recognised as a 3D mouse, but the browser exposes fewer than six axes for it. */
    | {kind: "noAxes"; name: string; vendorId: number; productId: number};

export interface GamepadSourceOptions {
    axis: AxisState;
    getGamepads: () => ArrayLike<PadLike | null> | null;
    /** Gets `gamepadconnected` / `gamepaddisconnected` (the window). */
    target: EventTarget | null;
    /** Pauses the idle timer while hidden (the document). */
    doc?: (EventTarget & {readonly hidden: boolean}) | null;
    /** The cap moved while nothing was reading it: the caller marks itself active and renders. */
    onWake: () => void;
    /** Buttons that went down since the last poll, as a bit mask. */
    onButtons: (pressed: number) => void;
    onStatus: (status: PadStatus) => void;
    now?: () => number;
    /** A pad drives this page only while it has focus, like the mouse and keyboard. */
    isFocused?: () => boolean;
    setInterval?: (fn: () => void, ms: number) => unknown;
    clearInterval?: (handle: unknown) => void;
    /** Idle poll period. */
    idleMs?: number;
}

export interface GamepadSource {
    start(): void;
    stop(): void;
    /** True while an opened WebHID device drives the axes instead. */
    setSuspended(suspended: boolean): void;
    /** Read the pad now. True when the cap is past rest. */
    poll(now?: number): boolean;
    status(): PadStatus;
    /** Whether this source is the one writing the axes right now. */
    driving(): boolean;
    /** Whether the idle timer is running (for tests and the debug hook). */
    idleTimerRunning(): boolean;
}

export const IDLE_POLL_MS = 50;

export function createGamepadSource(opts: GamepadSourceOptions): GamepadSource {
    const now = opts.now ?? (() => (typeof performance !== "undefined" ? performance.now() : Date.now()));
    const isFocused = opts.isFocused ?? (() => true);
    const setTimer = opts.setInterval ?? ((fn, ms) => setInterval(fn, ms));
    const clearTimer = opts.clearInterval ?? ((h) => clearInterval(h as ReturnType<typeof setInterval>));
    const idleMs = opts.idleMs ?? IDLE_POLL_MS;

    let started = false;
    let suspended = false;
    let timer: unknown = null;
    let status: PadStatus = {kind: "none"};
    let statusKey = "none";
    let hadPad = false;

    function publish(next: PadStatus): void {
        const key = next.kind === "none" ? "none" : `${next.kind}:${padKey(next.vendorId, next.productId)}:${next.name}`;
        if (key === statusKey) return;
        statusKey = key;
        status = next;
        opts.onStatus(next);
    }

    function scan() {
        const choice = selectPad(opts.getGamepads());
        if (!choice) {
            publish({kind: "none"});
        } else {
            const {name, vendorId, productId} = choice.info;
            publish({kind: choice.kind, name, vendorId, productId});
        }
        if ((!choice || choice.kind !== "active") && hadPad) {
            // The pad went away: a push in flight must not stay held. Only this source's
            // own readings are cleared -- while suspended the WebHID path owns the axes.
            hadPad = false;
            if (!suspended) resetAxes(opts.axis);
        }
        return choice;
    }

    function poll(at: number = now()): boolean {
        if (!started || suspended) return false;
        const choice = scan();
        if (!choice || choice.kind !== "active") return false;
        hadPad = true;
        if (!isFocused()) return false;
        const moved = readPad(choice.pad, padMapFor(choice.info.vendorId, choice.info.productId), opts.axis, at);
        const pressed = takePressedButtons(opts.axis);
        if (pressed !== 0) opts.onButtons(pressed);
        return moved;
    }

    function idleTick(): void {
        if (poll()) opts.onWake();
    }

    function reconcileTimer(): void {
        const want = started && !suspended && !opts.doc?.hidden;
        if (want && timer === null) timer = setTimer(idleTick, idleMs);
        else if (!want && timer !== null) {
            clearTimer(timer);
            timer = null;
        }
    }

    const onPadEvent = (): void => {
        if (started) scan();
    };
    const onVisibility = (): void => {
        reconcileTimer();
    };

    return {
        start() {
            if (started) return;
            started = true;
            opts.target?.addEventListener("gamepadconnected", onPadEvent);
            opts.target?.addEventListener("gamepaddisconnected", onPadEvent);
            opts.doc?.addEventListener("visibilitychange", onVisibility);
            scan();
            reconcileTimer();
        },
        stop() {
            if (!started) return;
            opts.target?.removeEventListener("gamepadconnected", onPadEvent);
            opts.target?.removeEventListener("gamepaddisconnected", onPadEvent);
            opts.doc?.removeEventListener("visibilitychange", onVisibility);
            started = false;
            reconcileTimer();
            hadPad = false;
            statusKey = "none";
            status = {kind: "none"};
        },
        setSuspended(value) {
            if (suspended === value) return;
            suspended = value;
            if (value) hadPad = false;
            if (!value && started) scan();
            reconcileTimer();
        },
        poll,
        status: () => status,
        driving: () => started && !suspended && status.kind === "active",
        idleTimerRunning: () => timer !== null,
    };
}
