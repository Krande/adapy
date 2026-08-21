import React from "react";

// A React error boundary — the only way to stop a render/lifecycle throw in one
// subtree from unmounting the WHOLE app. Without one, a single component bug
// (e.g. a zustand selector returning a fresh array each render → infinite loop,
// or a `.map` on undefined) white-screens the entire viewer. Wrap each
// independent surface (a side panel, the canvas, a modal) in its own boundary so
// one panel's crash is contained and the rest of the viewer keeps working.
//
// Boundaries MUST be class components (there is no hook equivalent for
// getDerivedStateFromError / componentDidCatch).

type FallbackRender = (error: Error, reset: () => void) => React.ReactNode;

type Props = {
  children: React.ReactNode;
  // Custom fallback UI. A render function receives the error + a reset callback
  // (call it to clear the error and re-mount the children). Omitted → a compact
  // default panel with a Retry button (or a full-page card for variant
  // "fullscreen").
  fallback?: React.ReactNode | FallbackRender;
  // When any value in this array changes (shallow compare), the boundary auto-
  // clears its error and retries — so, e.g., changing the selection after a
  // panel crashed lets the panel render again for the new input without a manual
  // Retry. Pass the inputs that drive the wrapped subtree.
  resetKeys?: ReadonlyArray<unknown>;
  // Shown in the default fallback ("<label> hit an error"). Also tags the
  // console.error so the offending surface is identifiable in logs.
  label?: string;
  // "panel" (default) = compact inline box sized for a side panel; "fullscreen"
  // = centered full-height card for the app-root last-resort boundary.
  variant?: "panel" | "fullscreen";
};

type State = { error: Error | null };

function keysChanged(
  a?: ReadonlyArray<unknown>,
  b?: ReadonlyArray<unknown>,
): boolean {
  if (a === b) return false;
  if (!a || !b || a.length !== b.length) return true;
  for (let i = 0; i < a.length; i++) if (!Object.is(a[i], b[i])) return true;
  return false;
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    // Keep the full error + component stack in the console so a contained crash
    // is still diagnosable (the fallback only shows a short message).
    console.error(
      `[ErrorBoundary${this.props.label ? `: ${this.props.label}` : ""}]`,
      error,
      info?.componentStack,
    );
  }

  componentDidUpdate(prev: Props): void {
    if (this.state.error && keysChanged(prev.resetKeys, this.props.resetKeys)) {
      this.reset();
    }
  }

  reset = (): void => this.setState({ error: null });

  render(): React.ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    const { fallback, label, variant = "panel" } = this.props;
    if (typeof fallback === "function")
      return (fallback as FallbackRender)(error, this.reset);
    if (fallback !== undefined) return fallback;

    const title = label ? `${label} hit an error` : "Something went wrong";
    if (variant === "fullscreen") {
      return (
        <div className="flex h-[100dvh] w-full items-center justify-center bg-surface-0 p-6 text-content">
          <div className="max-w-md rounded-md border border-fail bg-surface-0 p-4 text-sm">
            <div className="mb-1 font-semibold text-fail">{title}</div>
            <div className="mb-3 break-words text-content">
              {error.message}
            </div>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-sm bg-accent px-3 py-1 text-white pointer-fine:hover:bg-accent active:bg-accent-subtle"
            >
              Reload viewer
            </button>
          </div>
        </div>
      );
    }
    return (
      <div className="rounded-md border border-fail bg-surface-0 p-2 text-xs text-content">
        <div className="font-semibold text-fail">{title}</div>
        <div className="mt-0.5 mb-1.5 break-words text-content-muted">
          {error.message}
        </div>
        <button
          type="button"
          onClick={this.reset}
          className="rounded-sm bg-surface-2 px-2 py-1 text-white pointer-fine:hover:bg-surface-3 active:bg-surface-0"
        >
          Retry
        </button>
      </div>
    );
  }
}

export default ErrorBoundary;
