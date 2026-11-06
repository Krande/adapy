// The Desktop UI's title bar over core's canvas-less pages (`/convert`, `/admin`):
// the page's title and the way back to the viewer.

import React from "react";

import {DESKTOP_BAR} from "./desktopShell";

const DesktopPageHeader: React.FC<{title: string; backHref: string}> = ({title, backHref}) => (
  <div className={`${DESKTOP_BAR} h-9 border-b px-2 text-xs`} data-desktop-page-header="">
    <a href={backHref} className="rounded-sm px-2 py-1 hover:bg-white/10">
      ← Back to the viewer
    </a>
    <span aria-hidden="true" className="text-[var(--ada-panel-text-muted,#9ca3af)]">
      |
    </span>
    <h1 className="text-xs font-semibold">{title}</h1>
  </div>
);

export default DesktopPageHeader;
