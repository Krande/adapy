// In-app confirm / alert / prompt, in place of `window.confirm` / `alert` / `prompt`: those
// block the page, cannot be styled, and in an embedded viewer are titled with the host
// page's origin.
//
// Dependency-free, so it sits on the root `@/viewer-core` entry. Core's own dialogs are
// loaded on first use; a host that draws its own installs them with `setDialogHost`.

export type DialogTone = "default" | "danger";

export interface DialogText {
  title: string;
  /** Paragraphs; a string is split on newlines. */
  body?: string | readonly string[];
  /** "danger" for a destructive confirmation or a failure. */
  tone?: DialogTone;
}

export interface ConfirmOptions extends DialogText {
  confirmLabel?: string;
  cancelLabel?: string;
}

export interface AlertOptions extends DialogText {
  okLabel?: string;
}

export interface PromptOptions extends DialogText {
  /** The input's label. */
  label?: string;
  initial?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
}

/** What a host answers each request with: true / the text when accepted, false / null when declined. */
export interface DialogHost {
  confirm(options: ConfirmOptions): Promise<boolean>;
  alert(options: AlertOptions): Promise<void>;
  prompt(options: PromptOptions): Promise<string | null>;
}

let host: Partial<DialogHost> | null = null;

/** Installs a host's dialogs; a kind it leaves out stays core's. Returns the uninstall. */
export function setDialogHost(next: Partial<DialogHost> | null): () => void {
  host = next;
  return () => {
    if (host === next) host = null;
  };
}

const builtIn = async (): Promise<DialogHost> => (await import("@/components/ui/AppDialogs")).appDialogs;

/** Resolves true when the user accepts. */
export async function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  if (host?.confirm) return host.confirm(options);
  return (await builtIn()).confirm(options);
}

/** Resolves when the user has dismissed it. */
export async function alertDialog(options: AlertOptions): Promise<void> {
  if (host?.alert) return host.alert(options);
  return (await builtIn()).alert(options);
}

/** Resolves to the entered text, or null when declined. */
export async function promptDialog(options: PromptOptions): Promise<string | null> {
  if (host?.prompt) return host.prompt(options);
  return (await builtIn()).prompt(options);
}

/** A thrown value as dialog text. */
export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
