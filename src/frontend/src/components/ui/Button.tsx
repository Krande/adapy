import React from "react";
import {cn} from "./cn";

// The button family. Replaces ~375 hand-styled <button> elements whose colour,
// radius and padding were picked ad hoc per file (the M0 audit found the same
// visual role written as bg-blue-600, bg-blue-700 hover:bg-blue-600, rounded-sm,
// rounded-md and five different padding pairs).
//
// Two rules are baked in here so no call site has to remember them:
//
//   * `pointer-fine:hover:` — plain `hover:` sticks on touch devices: tapping
//     fires :hover and the highlight stays lit until you tap elsewhere. The
//     tailwind config sets hoverOnlyWhenSupported, and every hover style below
//     goes through it.
//   * touch targets — controls are 22/28/34px on a pointer device, but bump to
//     --ada-touch-h (40px) under `pointer: coarse`, via the `touch-target` class
//     defined in tokens.css consumers. Size classes below encode both.

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "subtle";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANT: Record<ButtonVariant, string> = {
    // Accent fill. The one call to action in a given region — not the default.
    primary:
        "bg-accent text-accent-fg border border-transparent " +
        "pointer-fine:hover:bg-accent-hover active:brightness-95",
    // The workhorse. Reads as a control against panel chrome without competing
    // with the 3D content for attention.
    secondary:
        "bg-surface-2 text-content border border-edge " +
        "pointer-fine:hover:bg-surface-3 pointer-fine:hover:border-edge-strong active:brightness-95",
    // No chrome until interacted with — toolbars, icon rows, table row actions.
    ghost:
        "bg-transparent text-content border border-transparent " +
        "pointer-fine:hover:bg-surface-2 active:brightness-95",
    // Destructive. Outlined rather than filled so a row of actions isn't dominated
    // by the one that deletes things.
    danger:
        "bg-transparent text-fail border border-fail/50 " +
        "pointer-fine:hover:bg-fail-subtle pointer-fine:hover:border-fail active:brightness-95",
    // Quietest: de-emphasised label, no border. Tertiary links and "cancel".
    subtle:
        "bg-transparent text-content-muted border border-transparent " +
        "pointer-fine:hover:text-content pointer-fine:hover:bg-surface-2",
};

const SIZE: Record<ButtonSize, string> = {
    sm: "h-control-sm min-h-control-sm px-2 gap-1 text-xs rounded-sm",
    md: "h-control-md min-h-control-md px-3 gap-1.5 text-sm rounded-md",
    lg: "h-control-lg min-h-control-lg px-4 gap-2 text-base rounded-md",
};

/** Shared by Button, IconButton and ToggleButton. */
export const BUTTON_BASE =
    "ada-focus inline-flex items-center justify-center shrink-0 font-medium whitespace-nowrap " +
    "select-none transition-colors duration-(--ada-dur-fast) " +
    "disabled:opacity-50 disabled:pointer-events-none";

/**
 * The exact classes `<Button variant size>` would apply, for the handful of places that
 * must stay a bare <button>.
 *
 * The cellbuilder is the case this exists for: several hundred lines of dense tool rows
 * where the elements carry their own refs, aria wiring and menu plumbing, and swapping
 * each for <Button> would be a rewrite rather than a re-chrome. Exporting the classes
 * means those buttons are styled BY the design system rather than to look like it —
 * there is one definition of what a secondary button is, and it lives above.
 *
 * Prefer <Button>. Reach for this only when you cannot change the element.
 */
export function buttonClasses(variant: ButtonVariant = "secondary", size: ButtonSize = "md"): string {
    return cn(BUTTON_BASE, VARIANT[variant], SIZE[size]);
}

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    variant?: ButtonVariant;
    size?: ButtonSize;
    /** Renders a spinner in place of `iconLeft` and disables the button. */
    loading?: boolean;
    iconLeft?: React.ReactNode;
    iconRight?: React.ReactNode;
    /** Stretch to the container width (form footers, menu items). */
    block?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
    {variant = "secondary", size = "md", loading, iconLeft, iconRight, block, className, children, disabled, type, ...rest},
    ref,
) {
    return (
        <button
            ref={ref}
            // Buttons inside a <form> default to type="submit", which has surprised
            // enough people that defaulting to "button" is the safer contract.
            type={type ?? "button"}
            disabled={disabled || loading}
            aria-busy={loading || undefined}
            className={cn(BUTTON_BASE, VARIANT[variant], SIZE[size], block && "w-full", className)}
            {...rest}
        >
            {loading ? <Spinner /> : iconLeft}
            {children}
            {iconRight}
        </button>
    );
});

/**
 * A square, label-less button.
 *
 * `tooltip` is REQUIRED by the type, not optional: an icon with no accessible name
 * is invisible to screen readers and guessable-at-best for everyone else. It becomes
 * both the `aria-label` and the title.
 */
export interface IconButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
    tooltip: string;
    icon: React.ReactNode;
    variant?: ButtonVariant;
    size?: ButtonSize;
    /** Toggle state. Sets aria-pressed and an active fill. */
    pressed?: boolean;
}

const ICON_SIZE: Record<ButtonSize, string> = {
    sm: "h-control-sm w-control-sm min-h-control-sm rounded-sm",
    md: "h-control-md w-control-md min-h-control-md rounded-md",
    lg: "h-control-lg w-control-lg min-h-control-lg rounded-md",
};

export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
    {tooltip, icon, variant = "ghost", size = "md", pressed, className, type, disabled, ...rest},
    ref,
) {
    const button = (
        <button
            ref={ref}
            type={type ?? "button"}
            aria-label={tooltip}
            // Only on the enabled button. When disabled the wrapper below carries it,
            // because a disabled element never fires the hover the tooltip needs.
            title={disabled ? undefined : tooltip}
            aria-pressed={pressed}
            disabled={disabled}
            className={cn(
                BUTTON_BASE,
                VARIANT[variant],
                ICON_SIZE[size],
                "p-0",
                pressed && "bg-accent-subtle text-accent border-accent/40",
                className,
            )}
            {...rest}
        >
            {icon}
        </button>
    );

    if (!disabled) return button;

    // A disabled button receives no pointer events — that is in BUTTON_BASE, and it is
    // also what browsers do natively — so its `title` never shows. Every greyed toolbar
    // icon was therefore unexplained, which defeats the entire point of greying with a
    // reason: the reason existed and was unreachable.
    //
    // Disabled controls use the DEFAULT cursor, not `not-allowed`.
    //
    // The 🚫 cursor says "this action is forbidden". Almost nothing here is forbidden — the
    // controls are temporarily inapplicable, which is a much milder claim: nothing is
    // selected yet, no result set is loaded. Dimming plus a tooltip that says which already
    // carries the message; a barred cursor on top of that is a scolding.
    //
    // Reserved for the genuine case: an action the user is not permitted to perform.
    //
    // The fix is a wrapper that is not disabled and so still gets hover. `inline-flex`
    // keeps it out of the layout's way (toolbars are flex rows and a plain span would
    // stretch), and the wrapper is aria-hidden-free but carries no role, so assistive
    // tech still reads the button's own disabled state rather than a phantom control.
    return (
        <span title={tooltip} className="inline-flex cursor-default">
            {button}
        </span>
    );
});

/** A labelled two-state toggle. Same shape as Button plus aria-pressed. */
export interface ToggleButtonProps extends ButtonProps {
    pressed: boolean;
}

export const ToggleButton = React.forwardRef<HTMLButtonElement, ToggleButtonProps>(function ToggleButton(
    {pressed, className, ...rest},
    ref,
) {
    return (
        <Button
            ref={ref}
            aria-pressed={pressed}
            className={cn(pressed && "bg-accent-subtle text-accent border-accent/40", className)}
            {...rest}
        />
    );
});

/** Inline loading indicator. Sized in em so it tracks the button's font size. */
export function Spinner({className}: {className?: string}) {
    return (
        <span
            aria-hidden="true"
            className={cn(
                "inline-block w-[1em] h-[1em] rounded-full border-2 border-current border-r-transparent",
                // motion-safe: under prefers-reduced-motion this renders as a static
                // ring rather than disappearing entirely, so the busy state is still legible.
                "motion-safe:animate-spin",
                className,
            )}
        />
    );
}
