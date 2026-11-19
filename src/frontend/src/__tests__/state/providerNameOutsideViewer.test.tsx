import assert from "node:assert/strict";
import {test} from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

// The admin page mounts no viewer, and Admin > Providers names providers through this hook.
// It used to read the scope through the viewer's context, so the whole Providers tab crashed
// ("useAdaViewerCtx() must be called inside an <AdaViewerProvider>") as soon as any provider
// advertised collections and its Access section rendered.

// The store bag reads the runtime config (`window.*`) as it is built; a bare object is enough.
(globalThis as Record<string, unknown>).window ??= {};
const {useProviderName} = await import("@/state/providerNamesStore");

function Named({id}: {id: string}) {
    const pn = useProviderName();
    return <span>{pn(id)}</span>;
}

test("useProviderName renders outside any viewer, falling back to the id", () => {
    assert.equal(renderToStaticMarkup(<Named id="fixture-provider" />), "<span>fixture-provider</span>");
});
