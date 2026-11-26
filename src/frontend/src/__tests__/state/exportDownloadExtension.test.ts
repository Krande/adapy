// The Genie XML export is a plain XML document; the format id/REST parameter/capability type
// keep "gxml" (unchanged), but the download must end in ".xml", matching what the other export
// route already writes. IFC/the Genie workspace are untouched.

import assert from "node:assert/strict";
import { test } from "node:test";

import { downloadExtensionFor } from "@/state/cellbuilder/exportImportSlice";

test("downloadExtensionFor: gxml downloads as .xml; ifc and gnx keep their own extension", () => {
  assert.equal(downloadExtensionFor("gxml"), "xml");
  assert.equal(downloadExtensionFor("ifc"), "ifc");
  assert.equal(downloadExtensionFor("gnx"), "gnx");
});
