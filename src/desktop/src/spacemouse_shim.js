// Injected into every page under ada-desktop. Gives the page a `navigator.hid` whose only device is the
// native 3D mouse, fed by the shell's `spacemouse://` events, so the viewer's existing WebHID path
// (utils/spacemouse/spaceMouseDevice.ts) works unchanged and never shows the browser chooser.
// Not injected with --no-spacemouse (the page then sees WebView2's own navigator.hid); does nothing on a
// page that has no IPC (anything not served from loopback).
(function () {
  if (!window.__TAURI_INTERNALS__ || window.__adaNativeHid) return;
  var ev = window.__TAURI__ && window.__TAURI__.event;
  if (!ev) return;
  window.__adaNativeHid = true;
  var dev = new EventTarget();
  // 0x256f is the current 3D mouse vendor id; the product id is a placeholder the frontend accepts.
  Object.assign(dev, { opened: false, vendorId: 0x256f, productId: 0xc635, productName: "3D mouse (native)",
    collections: [{ usagePage: 1, usage: 8 }],
    open: function () { dev.opened = true; return Promise.resolve(); },
    close: function () { dev.opened = false; return Promise.resolve(); } });
  function report(id, bytes) {
    var e = new Event("inputreport");
    e.device = dev; e.reportId = id; e.data = new DataView(bytes.buffer);
    dev.dispatchEvent(e);
  }
  var present = false;
  var hid = new EventTarget();
  hid.getDevices = function () { return Promise.resolve(present ? [dev] : []); };
  hid.requestDevice = function () { return Promise.resolve(present ? [dev] : []); };
  function setPresent(p) {
    if (p === present) return; present = p;
    var c = new Event(p ? "connect" : "disconnect"); c.device = dev; hid.dispatchEvent(c);
  }
  ev.listen("spacemouse://status", function (e) {
    var s = String(e.payload || "");
    if (s.indexOf("opened") === 0) setPresent(true); else if (s.indexOf("disconnected") >= 0) setPresent(false);
  });
  ev.listen("spacemouse://motion", function (e) {
    var m = e.payload, b = new Uint8Array(12), v = new DataView(b.buffer);
    ["tx", "ty", "tz", "rx", "ry", "rz"].forEach(function (k, i) { v.setInt16(i * 2, m[k] | 0, true); });
    report(1, b);
  });
  ev.listen("spacemouse://buttons", function (e) {
    var b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, e.payload.mask >>> 0, true); report(3, b);
  });
  // Ask the shell whether a device is already open (status events fired before the page loaded).
  window.__TAURI__.core.invoke("spacemouse_status").then(function (s) { if (s) setPresent(true); }).catch(function () {});
  Object.defineProperty(navigator, "hid", { value: hid, configurable: true });
})();
