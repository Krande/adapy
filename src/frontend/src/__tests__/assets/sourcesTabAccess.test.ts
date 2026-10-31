import assert from "node:assert/strict";
import { test } from "node:test";

import { sourcesTabAccess } from "@/assets/sourcesTabAccess";

test("a member is offered no requests, provider options, staging or raw files", () => {
    assert.deepEqual(sourcesTabAccess(false), { requests: false, providerOptions: false, staging: false, files: false });
});

test("an admin is offered all of them", () => {
    const access = sourcesTabAccess(true);
    assert.ok(Object.values(access).every(Boolean), JSON.stringify(access));
});
