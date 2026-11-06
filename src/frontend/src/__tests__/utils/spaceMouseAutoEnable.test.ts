// 3D-mouse support turns itself on when a device is there, until the user has
// chosen in Options: looking uses `getDevices()` only (devices the site was
// granted, or the desktop app's native reader), so it never prompts.

import assert from "node:assert/strict";
import { test } from "node:test";

import { VENDOR_ID } from "../../utils/spacemouse/hidReport";

type Listener = (e: Event) => void;

class FakeHid {
    devices: unknown[] = [];
    listeners = new Map<string, Set<Listener>>();
    getDevices() {
        return Promise.resolve(this.devices);
    }
    requestDevice() {
        return Promise.resolve([]);
    }
    addEventListener(type: string, fn: Listener) {
        if (!this.listeners.has(type)) this.listeners.set(type, new Set());
        this.listeners.get(type)!.add(fn);
    }
    removeEventListener(type: string, fn: Listener) {
        this.listeners.get(type)?.delete(fn);
    }
    fire(type: string, device: unknown) {
        for (const fn of [...(this.listeners.get(type) ?? [])]) fn({ device } as unknown as Event);
    }
}

function fakeDevice(): unknown {
    return {
        vendorId: VENDOR_ID,
        productId: 0xc652,
        productName: "3D mouse",
        opened: false,
        collections: [{ usagePage: 0x01, usage: 0x08 }],
        open() {
            (this as { opened: boolean }).opened = true;
            return Promise.resolve();
        },
        close() {
            return Promise.resolve();
        },
        addEventListener() {},
        removeEventListener() {},
    };
}

const hid = new FakeHid();
Object.defineProperty(globalThis, "navigator", { value: { hid }, configurable: true, writable: true });
// The store persists its options; give it somewhere to write.
const mem = new Map<string, string>();
Object.defineProperty(globalThis, "localStorage", {
    value: {
        getItem: (k: string) => mem.get(k) ?? null,
        setItem: (k: string, v: string) => void mem.set(k, v),
        removeItem: (k: string) => void mem.delete(k),
    },
    configurable: true,
    writable: true,
});

const flush = () => new Promise((r) => setTimeout(r, 0));

const { useSpaceMouseStore } = await import("../../state/spaceMouseStore");
const { followAutoEnable: bindSpaceMouseToOptions } = await import("../../utils/spacemouse/autoEnable");

function reset(state: { enabled: boolean; chosen: boolean }) {
    useSpaceMouseStore.setState(state);
    hid.devices = [];
}

test("no device: support stays off and nothing is chosen", async () => {
    reset({ enabled: false, chosen: false });
    const unbind = bindSpaceMouseToOptions();
    await flush();
    assert.equal(useSpaceMouseStore.getState().enabled, false);
    assert.equal(useSpaceMouseStore.getState().chosen, false);
    unbind();
});

test("a device the page may already use turns support on, without counting as a choice", async () => {
    reset({ enabled: false, chosen: false });
    hid.devices = [fakeDevice()];
    const unbind = bindSpaceMouseToOptions();
    await flush();
    assert.equal(useSpaceMouseStore.getState().enabled, true);
    assert.equal(useSpaceMouseStore.getState().chosen, false);
    unbind();
});

test("a device plugged in later turns it on too", async () => {
    reset({ enabled: false, chosen: false });
    const unbind = bindSpaceMouseToOptions();
    await flush();
    assert.equal(useSpaceMouseStore.getState().enabled, false);
    hid.fire("connect", fakeDevice());
    assert.equal(useSpaceMouseStore.getState().enabled, true);
    unbind();
});

test("switched off in Options, a found device leaves it off", async () => {
    reset({ enabled: false, chosen: false });
    useSpaceMouseStore.getState().setEnabled(false);
    assert.equal(useSpaceMouseStore.getState().chosen, true);
    hid.devices = [fakeDevice()];
    const unbind = bindSpaceMouseToOptions();
    await flush();
    hid.fire("connect", fakeDevice());
    assert.equal(useSpaceMouseStore.getState().enabled, false);
    unbind();
});
