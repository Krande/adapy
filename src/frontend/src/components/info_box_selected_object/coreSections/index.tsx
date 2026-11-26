// Core's own sections of the Selected Object panel, as providers, in the order the panel has
// always drawn them. Each section's own file holds its render; `match.ts` holds the rules
// (MATCH, keyed by id) they are shown under.

import React from "react";
import type {PropertyProvider} from "@/plugins/propertyProviders";
import {MATCH} from "./match";
import {SelectionSummary} from "./selectionSummary";
import {ClashContact} from "./clashContact";
import {FemConcepts} from "./femConcepts";
import {SourceJump} from "./sourceJump";
import {ObjectMetadata} from "./objectMetadata";
import {FeaSets, FlatFeaElement} from "./feaElement";
import {IndexedAttributes} from "./indexedAttributes";
import {Related} from "./related";
import {ElementLinks} from "./elementLinks";
import {TreeNode} from "./treeNode";
import {CellbuilderItem, CellbuilderStructure} from "./cellbuilder";

export const CORE_PROPERTY_PROVIDERS: readonly PropertyProvider[] = [
  {id: "selection-summary", order: 0, match: MATCH["selection-summary"], render: (sel) => <SelectionSummary sel={sel} />},
  {id: "clash-contact", order: 5, match: MATCH["clash-contact"], render: () => <ClashContact />},
  {id: "fem-concepts", order: 10, match: MATCH["fem-concepts"], render: () => <FemConcepts />},
  {id: "source-jump", order: 20, match: MATCH["source-jump"], render: () => <SourceJump />},
  {id: "object-metadata", order: 30, match: MATCH["object-metadata"], render: () => <ObjectMetadata />},
  {id: "fea-element", order: 32, match: MATCH["fea-element"], render: (sel) => <FlatFeaElement name={sel.meshName} />},
  {id: "fea-sets", order: 35, match: MATCH["fea-sets"], render: () => <FeaSets />},
  {id: "indexed-attributes", order: 40, match: MATCH["indexed-attributes"], render: () => <IndexedAttributes />},
  {id: "related", order: 50, match: MATCH["related"], render: () => <Related />},
  {id: "element-links", order: 55, match: MATCH["element-links"], render: (sel) => <ElementLinks sel={sel} />},
  {id: "tree-node", order: 60, match: MATCH["tree-node"], render: () => <TreeNode />},
  {id: "cellbuilder-item", order: 70, match: MATCH["cellbuilder-item"], render: () => <CellbuilderItem />},
  {id: "cellbuilder-structure", order: 74, match: MATCH["cellbuilder-structure"], render: () => <CellbuilderStructure />},
];
