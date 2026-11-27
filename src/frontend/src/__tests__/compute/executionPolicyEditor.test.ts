// The admin editor's state rules (components/admin/execution/executionPolicyEditor.ts).
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  addRow,
  missingEngines,
  moveEngine,
  policyFromRows,
  removeRow,
  rowErrors,
  rowsFromPolicy,
  samePolicy,
  toggleEngine,
  updateRow,
} from "@/components/admin/execution/executionPolicyEditor";
import { defaultExecutionPolicy } from "@/services/compute/executionPolicyModel";

describe("execution policy editor", () => {
  it("round-trips a document through rows, in display order", () => {
    const doc = defaultExecutionPolicy();
    const rows = rowsFromPolicy(doc);
    assert.deepEqual(policyFromRows(rows), doc);
    // By operation, "*" first, then the qualifiers A-Z.
    assert.deepEqual(
      rows.filter((r) => r.op === "read").map((r) => r.qualifier),
      ["*", ".ifc", ".step", ".stp"],
    );
    assert.equal(rows[0].op, "convert");
    assert.ok(samePolicy(policyFromRows([...rows].reverse()), doc));
  });

  it("reorders, removes and adds engines", () => {
    assert.deepEqual(moveEngine(["native", "pyodide", "worker"], 2, -1), ["native", "worker", "pyodide"]);
    assert.deepEqual(moveEngine(["native", "pyodide"], 0, -1), ["native", "pyodide"]);
    assert.deepEqual(toggleEngine(["native", "pyodide"], "native"), ["pyodide"]);
    assert.deepEqual(toggleEngine(["pyodide"], "worker"), ["pyodide", "worker"]);
    assert.deepEqual(missingEngines(["pyodide"]), ["native", "worker"]);
    const rows = updateRow(rowsFromPolicy(defaultExecutionPolicy()), "fea_bake", "*", ["worker"]);
    assert.deepEqual(policyFromRows(rows).fea_bake, { "*": ["worker"] });
  });

  it("adds qualifier rows starting from the order that answers them now", () => {
    const rows = rowsFromPolicy(defaultExecutionPolicy());
    const added = addRow(rows, "read", ".OBJ");
    assert.ok("rows" in added);
    if ("rows" in added) {
      assert.deepEqual(policyFromRows(added.rows).read?.[".obj"], ["pyodide", "worker"]);
      assert.ok("error" in addRow(added.rows, "read", ".obj"));
    }
    assert.ok("error" in addRow(rows, "read", "obj"));
    const plugin = addRow(rows, "plugin_job", "example-plugin", ["worker"]);
    assert.ok("rows" in plugin && policyFromRows(plugin.rows).plugin_job?.["example-plugin"]?.[0] === "worker");
  });

  it("keeps the global '*' rows; an override may drop them", () => {
    const rows = rowsFromPolicy(defaultExecutionPolicy());
    assert.equal(removeRow(rows, "read", "*").length, rows.length);
    assert.equal(removeRow(rows, "read", ".ifc").length, rows.length - 1);
    assert.equal(removeRow(rows, "read", "*", false).length, rows.length - 1);
  });

  it("reports what the server would refuse", () => {
    assert.deepEqual(rowErrors(rowsFromPolicy(defaultExecutionPolicy())), []);
    const bad = updateRow(rowsFromPolicy(defaultExecutionPolicy()), "convert", "*", []);
    assert.ok(rowErrors(bad).some((e) => /at least one engine/.test(e)));
  });
});
