// The Files tab: today's selection tree on its unchanged store. It exists as a
// name so the shell reads as two tabs; there is no logic here on purpose.

import React from "react";

import TreeViewComponent, { type SceneTreeProps } from "@/components/tree_view/TreeViewComponent";

const FilesTab: React.FC<SceneTreeProps> = (props) => <TreeViewComponent {...props} />;

export default FilesTab;
