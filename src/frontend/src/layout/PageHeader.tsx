// The title bar a composed shell draws over core's canvas-less pages (`/convert`, `/admin`): the
// page's title and the way back to the viewer, with a slot for a scope picker (the pages work in
// the active scope) and one for the shell's own controls. A shell names a component of its own in
// `UiShellChrome.pageHeader`, or lists this one's pieces; the Desktop UI mounts it as is.

import React from "react";

import {SHELL_BAR} from "./statusItems";

export interface PageHeaderProps {
  title: string;
  backHref: string;
  /** The link back to the viewer. Default "Back to the viewer". */
  backLabel?: string;
  /** A scope picker (or any control that belongs after the title): drawn at the right end. */
  scopePicker?: React.ReactNode;
  /** More of the shell's own controls, between the title and the scope picker. */
  trailing?: React.ReactNode;
  /** Replaces the bar's look (default `SHELL_BAR`, 36px high). */
  className?: string;
}

export const PageHeader: React.FC<PageHeaderProps> = ({title, backHref, backLabel = "Back to the viewer", scopePicker, trailing, className}) => (
  <div className={className ?? `${SHELL_BAR} h-9 border-b px-2 text-xs`} data-page-header="">
    <a href={backHref} className="rounded-sm px-2 py-1 hover:bg-white/10">
      ← {backLabel}
    </a>
    <span aria-hidden="true" className="text-[var(--ada-panel-text-muted,#9ca3af)]">
      |
    </span>
    <h1 className="text-xs font-semibold">{title}</h1>
    {trailing}
    {scopePicker && (
      <div className="ml-auto flex items-center gap-2" data-page-header-scope="">
        {scopePicker}
      </div>
    )}
  </div>
);

export default PageHeader;
