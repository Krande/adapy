// The "object-metadata" section: ObjectMetadataPanel takes its payload as a prop; this wrapper
// reads the store and the panel's own options (flat, summary) for it.

import {useViewerStores} from "@/state/AdaViewerContext";
import ObjectMetadataPanel from "../ObjectMetadataPanel";
import {ObjectInfoOptions} from "../objectInfoOptions";
import React from "react";

export const ObjectMetadata: React.FC = () => {
  const {useObjectInfoStore} = useViewerStores();
  const {flat, summary} = React.useContext(ObjectInfoOptions);
  const jsonData = useObjectInfoStore((s) => s.jsonData);
  return <ObjectMetadataPanel data={jsonData} flat={flat} menuSummary={summary === "menu"} />;
};
