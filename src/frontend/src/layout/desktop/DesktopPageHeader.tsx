// The Desktop UI's title bar over core's canvas-less pages (`/convert`, `/admin`): core's generic
// `PageHeader` (`../PageHeader`), the shell's own look being the window tokens it sets.

import React from "react";

import {PageHeader} from "../PageHeader";

const DesktopPageHeader: React.FC<{title: string; backHref: string}> = (props) => <PageHeader {...props} />;

export default DesktopPageHeader;
