// The "element-links" section: what has been found, built and simulated under the selection.

import React from "react";
import type {SelectionSnapshot} from "@/plugins/propertyProviders";
import ElementLinksSection from "../ElementLinksSection";

export const ElementLinks: React.FC<{sel: SelectionSnapshot}> = ({sel}) => <ElementLinksSection sel={sel} />;
