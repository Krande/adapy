import {test} from "node:test";
import assert from "node:assert/strict";
import {AD_HOC_DEFAULT_SCOPE, defaultAuditScope} from "@/components/admin/auditRuns/defaultScope";

test("the form opens on the first corpus when the deployment has one", () => {
    assert.equal(defaultAuditScope([{slug: "basic"}, {slug: "hull"}]), "corpus:basic");
});

test("without a corpus the form opens on the ad-hoc shared scope", () => {
    assert.equal(defaultAuditScope([]), AD_HOC_DEFAULT_SCOPE);
    assert.equal(AD_HOC_DEFAULT_SCOPE, "shared");
});

test("a corpus without a slug is skipped", () => {
    assert.equal(defaultAuditScope([{slug: ""}, {slug: "basic"}]), "corpus:basic");
});
