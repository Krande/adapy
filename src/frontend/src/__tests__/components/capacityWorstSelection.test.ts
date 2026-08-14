import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  girderLineTint,
  isSameResultRow,
  resolveWorstSelection,
  worstCoverageLabel,
  type CapacityCaseResultLike,
} from "../../components/capacity/capacityFormat";
import {
  needsSpineReload,
  progressPollFailureAction,
  readyCaseIds,
  worstStringTableChoice,
  type CapacityCalcProgress,
} from "../../state/capacityResultsStore";

/** A compact worst-view row: keyed (model, stiffener), carrying the case its
 *  maximum came from and no per-check detail. */
function worstRow(
  model: string,
  stiffener: string,
  caseId: string,
  uf: number,
  extra: Partial<CapacityCaseResultLike> = {},
): CapacityCaseResultLike {
  return {
    id: `${model}::${stiffener}`,
    case_id: caseId,
    capacity_model_id: model,
    panel_group: `panelGroup(${stiffener})`,
    stiffener,
    governing_usage: uf,
    passed: uf < 1,
    checks: [],
    ...extra,
  } as CapacityCaseResultLike;
}

/** The full row for the same item inside one case's detail file: its own
 *  case-qualified id, and real checks. */
function detailRow(
  model: string,
  stiffener: string,
  caseId: string,
  uf: number,
): CapacityCaseResultLike {
  return {
    id: `${caseId}::${model}::${stiffener}`,
    case_id: caseId,
    capacity_model_id: model,
    panel_group: `panelGroup(${stiffener})`,
    stiffener,
    governing_usage: uf,
    passed: uf < 1,
    checks: [
      {
        id: "plate",
        label: "Plate buckling",
        usage: uf,
        passed: uf < 1,
      },
    ],
  } as CapacityCaseResultLike;
}

const WORST_ROWS = [
  worstRow("model-a", "Stiff_1", "16", 2.91),
  worstRow("model-a", "Stiff_2", "20", 1.4),
  worstRow("model-b", "Stiff_3", "16", 0.6),
];

describe("resolveWorstSelection", () => {
  it("resolves the clicked row and reports the case its maximum came from", () => {
    const { worstRow: hit, row } = resolveWorstSelection({
      worstRows: WORST_ROWS,
      caseDetail: {},
      selectedResultId: "model-a::Stiff_2",
      selectedModelId: "model-a",
    });

    // The case is reported so the caller can fetch that detail file -- but
    // resolving must never imply switching the active case (issue #35).
    assert.equal(hit?.case_id, "20");
    assert.equal(hit?.stiffener, "Stiff_2");
    // Detail not loaded yet: fall back to the compact row so the panel is
    // never blank.
    assert.equal(row, hit);
  });

  it("prefers the governing case's detailed row once it has loaded", () => {
    const { worstRow: hit, row } = resolveWorstSelection({
      worstRows: WORST_ROWS,
      caseDetail: {
        "20": [
          detailRow("model-b", "Stiff_3", "20", 0.1),
          detailRow("model-a", "Stiff_2", "20", 1.4),
        ],
      },
      selectedResultId: "model-a::Stiff_2",
      selectedModelId: "model-a",
    });

    assert.equal(hit?.case_id, "20");
    // Matched across differing id schemes: worst rows are keyed
    // (model, stiffener) while detail rows carry a case-qualified id.
    assert.notEqual(row, hit);
    assert.equal(row?.id, "20::model-a::Stiff_2");
    assert.equal(row?.checks.length, 1);
  });

  it("does not borrow a detailed row from a different case", () => {
    const { row } = resolveWorstSelection({
      worstRows: WORST_ROWS,
      caseDetail: { "16": [detailRow("model-a", "Stiff_2", "16", 0.3)] },
      selectedResultId: "model-a::Stiff_2",
      selectedModelId: "model-a",
    });

    // Stiff_2's worst came from case 20, so case 16's row must not be used.
    assert.equal(row?.governing_usage, 1.4);
    assert.equal(row?.checks.length, 0);
  });

  it("falls back to the model's worst row when only a model is selected", () => {
    // Picking in the 3D view selects a model, not a specific row.
    const { worstRow: hit } = resolveWorstSelection({
      worstRows: WORST_ROWS,
      caseDetail: {},
      selectedResultId: null,
      selectedModelId: "model-a",
    });

    assert.equal(hit?.stiffener, "Stiff_1");
    assert.equal(hit?.governing_usage, 2.91);
  });

  it("ranks an errored row above every finite utilization", () => {
    const errored = worstRow("model-a", "Stiff_9", "31", 0, {
      error: "FormulaDomainError: [6.21] negative radicand",
      passed: false,
    });
    const { worstRow: hit } = resolveWorstSelection({
      worstRows: [...WORST_ROWS, errored],
      caseDetail: {},
      selectedResultId: null,
      selectedModelId: "model-a",
    });

    assert.equal(hit?.stiffener, "Stiff_9");
  });

  it("returns nothing when there is no selection", () => {
    const { worstRow: hit, row } = resolveWorstSelection({
      worstRows: WORST_ROWS,
      caseDetail: {},
      selectedResultId: null,
      selectedModelId: null,
    });

    assert.equal(hit, null);
    assert.equal(row, null);
  });
});

describe("isSameResultRow", () => {
  it("matches a worst row against the same item's detail row", () => {
    // The table lists worst rows keyed (model, stiffener) while the selection
    // resolves to a case-qualified detail row. Comparing keys leaves the
    // selected row unhighlighted (#46); identity is what actually matches.
    const worst = worstRow("model-a", "Stiff_2", "20", 1.4);
    const detail = detailRow("model-a", "Stiff_2", "20", 1.4);

    assert.notEqual(worst.id, detail.id);
    assert.ok(isSameResultRow(worst, detail));
  });

  it("matches the same item across different cases", () => {
    assert.ok(
      isSameResultRow(
        detailRow("model-a", "Stiff_2", "16", 0.3),
        detailRow("model-a", "Stiff_2", "20", 1.4),
      ),
    );
  });

  it("separates different stiffeners in the same capacity model", () => {
    assert.equal(
      isSameResultRow(
        worstRow("model-a", "Stiff_1", "16", 2.91),
        worstRow("model-a", "Stiff_2", "20", 1.4),
      ),
      false,
    );
  });

  it("is false when either row is missing", () => {
    const row = worstRow("model-a", "Stiff_1", "16", 2.91);
    assert.equal(isSameResultRow(null, row), false);
    assert.equal(isSameResultRow(row, undefined), false);
    assert.equal(isSameResultRow(null, null), false);
  });
});

describe("readyCaseIds", () => {
  const progress = (over: Partial<CapacityCalcProgress> = {}) =>
    ({
      complete: false,
      phase: "checking",
      cases_total: 4,
      cases_done: 2,
      runs: [
        { id: "run-001", scope: "stiffened_panel", cases_total: 2, cases_ready: ["9"], complete: false },
        { id: "run-002", scope: "girder", cases_total: 2, cases_ready: ["g9", "g10"], complete: true },
      ],
      ...over,
    }) as CapacityCalcProgress;

  it("reports only the cases a run has published", () => {
    const ready = readyCaseIds(progress(), "run-001");
    assert.ok(ready?.has("9"));
    assert.equal(ready?.has("10"), false);
  });

  it("is per run, so a finished girder run reads as fully available", () => {
    const ready = readyCaseIds(progress(), "run-002");
    assert.deepEqual([...(ready ?? [])].sort(), ["g10", "g9"]);
  });

  it("returns null once the calculation completes", () => {
    // null means "not streaming" — every case is available and the UI stops
    // marking anything as in progress.
    assert.equal(readyCaseIds(progress({ complete: true }), "run-001"), null);
  });

  it("returns null when nothing is streaming at all", () => {
    assert.equal(readyCaseIds(null, "run-001"), null);
  });
});

describe("progressPollFailureAction", () => {
  const limits = { initialAttempts: 3, maxFailures: 10 };

  it("keeps following the run after a single failed read", () => {
    // The calculation rewrites the progress file constantly; one unreadable
    // poll is a race, not the end of the run. Treating it as the end cleared
    // the run status mid-run and left the worst view asking for cases that did
    // not exist yet ("Could not load 84 of 84").
    assert.equal(progressPollFailureAction(true, 1, limits), "retry");
    assert.equal(progressPollFailureAction(true, 9, limits), "retry");
  });

  it("keeps the last status on screen when a run goes quiet for good", () => {
    assert.equal(progressPollFailureAction(true, 10, limits), "stop-keep");
  });

  it("retries a few times before concluding no run is streaming", () => {
    // The viewer can open a moment before the calculation's first publish.
    assert.equal(progressPollFailureAction(false, 1, limits), "retry");
    assert.equal(progressPollFailureAction(false, 2, limits), "retry");
  });

  it("stops quietly for an ordinary finished bundle", () => {
    assert.equal(progressPollFailureAction(false, 3, limits), "stop-no-run");
  });
});

describe("needsSpineReload", () => {
  const publishing = (id: string, ready: string[]) =>
    ({
      id,
      scope: id === "run-002" ? "girder" : "stiffened_panel",
      cases_total: 84,
      cases_ready: ready,
      complete: false,
    }) as CapacityCalcProgress["runs"][number];

  it("re-reads when a run has published cases the spine does not have", () => {
    // Both runs are announced before either starts, so the old "did the number
    // of runs change?" test was false from the first poll onwards and the
    // girder run never reached the Capacity check type dropdown.
    assert.equal(
      needsSpineReload(
        [publishing("run-001", ["9"]), publishing("run-002", ["g9"])],
        ["run-001"],
        true,
        false,
      ),
      true,
    );
  });

  it("leaves the spine alone when every publishing run is already in it", () => {
    assert.equal(
      needsSpineReload(
        [publishing("run-001", ["9", "10"]), publishing("run-002", ["g9"])],
        ["run-001", "run-002"],
        true,
        false,
      ),
      false,
    );
  });

  it("does not re-read for a run that has been announced but published nothing", () => {
    // The girder run shows as queued in the run status; there is nothing to put
    // in the spine for it yet.
    assert.equal(
      needsSpineReload(
        [publishing("run-001", ["9"]), publishing("run-002", [])],
        ["run-001"],
        true,
        false,
      ),
      false,
    );
  });

  it("always re-reads before there is a spine, and once the run completes", () => {
    assert.equal(needsSpineReload([], [], false, false), true);
    assert.equal(needsSpineReload([], ["run-001"], true, true), true);
  });

  it("re-reads when the run has rewritten the spine since it was loaded", () => {
    // Runs enter the spine as their *definitions* are published, before they
    // have checked anything — which the cases_ready reading cannot see. A check
    // that had been reconstructed but not yet run therefore stayed out of the
    // "Capacity check type" dropdown for the whole run.
    assert.equal(
      needsSpineReload(
        [publishing("run-001", ["9"]), publishing("run-003", [])],
        ["run-001"],
        true,
        false,
        { published: 4, loaded: 2 },
      ),
      true,
    );
  });

  it("leaves the spine alone while the revision it holds is current", () => {
    assert.equal(
      needsSpineReload(
        [publishing("run-001", ["9"]), publishing("run-003", [])],
        ["run-001", "run-003"],
        true,
        false,
        { published: 4, loaded: 4 },
      ),
      false,
    );
  });

  it("reads the spine once when a revision is published but none is loaded", () => {
    assert.equal(
      needsSpineReload([publishing("run-001", [])], ["run-001"], true, false, {
        published: 1,
        loaded: null,
      }),
      true,
    );
  });

  it("falls back to the published-cases reading for a sidecar with no revision", () => {
    assert.equal(
      needsSpineReload(
        [publishing("run-001", ["9"]), publishing("run-002", ["g9"])],
        ["run-001"],
        true,
        false,
        { published: undefined, loaded: null },
      ),
      true,
    );
    assert.equal(
      needsSpineReload([publishing("run-001", ["9"])], ["run-001"], true, false, {
        published: undefined,
        loaded: null,
      }),
      false,
    );
  });
});

describe("girderLineTint", () => {
  it("colours a girder by its usage factor when it has one", () => {
    assert.equal(girderLineTint(0.42, true), "uf");
    assert.equal(girderLineTint(0, true), "uf");
  });

  it("marks a girder with no result yet as such, not as nearly overutilised", () => {
    // The definition amber (#F59E0B) is all but identical to the #FFA400 of the
    // 0.8-1.0 band, so falling back to it made un-computed girders look like
    // they were running at 0.8-1.0.
    assert.equal(girderLineTint(null, true), "no-result");
    assert.equal(girderLineTint(undefined, true), "no-result");
  });

  it("keeps the definition colour when results are not being shown", () => {
    assert.equal(girderLineTint(null, false), "definition");
  });
});

describe("worstStringTableChoice", () => {
  it("reads the streaming table once the run has interned more names", () => {
    // The spine's copy is written when the run's first case lands; later cases
    // can add governing-check names it never saw, and those decode to blank.
    assert.equal(worstStringTableChoice(9000, null, 9012), "refetch");
  });

  it("refetches when the cached copy has fallen behind", () => {
    assert.equal(worstStringTableChoice(9000, 9010, 9012), "refetch");
  });

  it("reuses the cached copy while nothing new has been published", () => {
    assert.equal(worstStringTableChoice(9000, 9012, 9012), "cached");
  });

  it("stays on the spine when it already covers everything published", () => {
    // The common case by far: case one interns essentially every name.
    assert.equal(worstStringTableChoice(9000, null, 9000), "spine");
  });

  it("prefers the spine when it is the longer of the two", () => {
    // After the final spine rewrite it is the complete table.
    assert.equal(worstStringTableChoice(9012, 9000, 9000), "spine");
  });
});

describe("worstCoverageLabel", () => {
  it("says the table is complete when every ticked case is in it", () => {
    assert.equal(worstCoverageLabel(84, 84, false), "Cases in this table");
  });

  it("flags a provisional maximum while cases are still being calculated", () => {
    // The worst value is taken over what has landed; without this the number
    // reads as final when it is not.
    assert.match(worstCoverageLabel(62, 84, false), /still being calculated/);
  });

  it("reports the load itself while shards are being fetched", () => {
    assert.equal(worstCoverageLabel(62, 84, true), "Loading result cases");
  });

  it("does not claim a complete table when nothing is selected", () => {
    assert.equal(worstCoverageLabel(0, 0, false), "No result cases selected");
  });
});
