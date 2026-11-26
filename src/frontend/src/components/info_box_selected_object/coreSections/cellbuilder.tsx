// The "cellbuilder-item" and "cellbuilder-structure" sections: procedural cellbuilder selection
// detail (cellBuilderSelection/BuilderSections), here, not in the busy cellbuilder tool panel --
// the picked item as one layout -- where it is (its space and structure), what it is, its box and
// its fields, or a plugin's ref --, and a structure on its own (the active one, with nothing
// picked). A plugin's section slots before, between or after them by order.

import React from "react";
import {BuilderItemSection, BuilderStructureSection} from "../cellBuilderSelection/BuilderSections";

export const CellbuilderItem: React.FC = () => <BuilderItemSection />;
export const CellbuilderStructure: React.FC = () => <BuilderStructureSection />;
