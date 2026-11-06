# ada-desktop

A native window for the adapy local viewer (Ada Studio). It is a small [Tauri 2](https://v2.tauri.app/)
app that embeds **no** frontend: it opens a window on a viewer that is already running on loopback,
normally the one `ada desktop` starts. Everything the viewer does still happens in the page and the
local REST server; the shell adds native windows, remembered window geometry and a native 3D mouse
reader.

Windows first (WebView2). The code is not Windows-specific beyond the HID backend and a few
message-box/shell calls, but other platforms are untested.

## Run it

```bash
ada desktop                    # start the local viewer and open it in a window
ada desktop model.ifc          # ... and open a file in it
```

`ada desktop` (in `src/ada_cli/desktop.py`) does the orchestration:

1. picks the port (see [Port policy](#port-policy));
2. starts `python -m ada.comms.rest` bound to `127.0.0.1` with local storage, no login, no job queue
   and a per-launch shutdown token, serving the viewer build shipped in ada-py (`index.zip`, unpacked
   once per content hash under the user cache directory) or `--static DIR` / `--zip FILE` / `--viewer NAME`;
3. waits for `/api/config`, optionally uploads FILE into the personal scope (`PUT
   /api/scopes/user:me/blobs/<name>`) and opens `/?scope=user:me&file=<name>`;
4. starts `ada-desktop --url <viewer url> --title <title>` and waits for it;
5. when the last window closes, stops the viewer: `POST /api/local/shutdown` with the token, then
   terminate, then kill. On Windows a job object also ends the viewer and the shell when `ada desktop`
   itself is killed.

The shell can also be started by hand on any loopback viewer:

```
ada-desktop --url http://127.0.0.1:PORT/ [--title T] [--native-drop] [--no-spacemouse]
ada-desktop --spacemouse-log
ada-desktop --version
```

| flag | |
| --- | --- |
| `--url URL` | The viewer to open. Must be `http` on `127.0.0.1`, `localhost` or `[::1]`. |
| `--title T` | Main window title (default `Ada Studio`). Pop-outs take their page's title. |
| `--native-drop` | The shell takes dropped files (native paths, logged only for now) instead of the page. By default the page gets `File` objects, as in a browser. |
| `--no-spacemouse` | Do not read the 3D mouse natively; the shim is not injected and the page sees WebView2's own WebHID. |
| `--spacemouse-log` | No window: list the 3D mouse HID interfaces and print what the device sends. |
| `--version` | Print the version and the WebView2 version (or `not installed`) and exit 0. Needs no display and no WebView2. |

`ada desktop --shell-arg=--no-spacemouse` passes a flag through.

## Port policy

The viewer keeps layouts, settings and other UI state in browser storage (localStorage, IndexedDB),
which belongs to the page's **origin**, and the origin includes the port. A random port per launch
would start every window from scratch, so `ada desktop` uses a fixed port:

* `--port N`, else `$ADA_DESKTOP_PORT`, else a per-user default:
  `38500 + crc32(lower-case user name) % 500`, i.e. somewhere in 38500-38999 (`ada desktop --help`
  prints yours). Per user so that two people on one machine do not collide; below every OS's
  ephemeral range and clear of the usual dev-server ports.
* If that port is free, the viewer starts there.
* If it is held by **a viewer another `ada desktop` started** (detected by `GET /api/config`: adapy's
  REST app, `transport: "rest"`, offering this page the local shutdown route), nothing new is started:
  this command opens **one more window** on the running viewer and its `--data`/`--static`/`--zip`/
  `--viewer` are ignored (it says so). It registers itself under `<cache>/clients/<port>/<pid>`; the
  launch that owns the viewer stops it only after its own window **and** every attached one have
  closed. Killing the owning launch outright still ends the viewer for everyone.
* If it is held by **anything else**, this launch uses a free port and prints a warning: its
  window starts without the stored layouts/settings, and changes made there are not seen on the
  usual port. Free the port or choose another fixed one to avoid that.

Directories (Windows): data `%LOCALAPPDATA%\ada\desktop\data`, cache (unpacked viewer builds, attached
window registry) `%LOCALAPPDATA%\ada\desktop\cache`. WebView2 keeps its profile, and with it the
browser storage, under `%LOCALAPPDATA%\org.adapy.desktop`. Old unpacked builds are not deleted
automatically (a running viewer may still serve one); the cache directory is safe to delete while
nothing runs.

## 3D mouse

`src/spacemouse.rs` reads the device with hidapi on a background thread (no vendor driver or SDK,
no WebHID chooser): it finds the multi-axis interface (usage page 0x01, usage 0x08) of a known 3D
mouse, parses reports 1/2/3 like the frontend's `hidReport.ts`, coalesces motion to about 60 Hz and
emits `spacemouse://status|motion|buttons` events. `src/spacemouse_shim.js`, injected into every
window, turns those events into a `navigator.hid` with one device, so the viewer's existing WebHID
code path works unchanged. If the vendor's driver holds the device exclusively the status says so;
quit the driver or run with `--no-spacemouse`.

## Security model

* **Navigation allow-list.** Every window (main and pop-outs) may only show the viewer's own origin
  (exactly the scheme, host and port of `--url`), its `blob:` URLs, or `about:blank`. Any other
  navigation is cancelled; `http`, `https` and `mailto` links are handed to the system browser
  instead, anything else (e.g. `file:`, custom protocol handlers) is dropped. `window.open` to another
  site likewise opens in the system browser rather than in a native window.
* **IPC for loopback only, minimal.** `capabilities/viewer.json` grants remote IPC to
  `http://127.0.0.1:*` and `http://localhost:*` pages only, and only `core:event:allow-listen`,
  `core:event:allow-unlisten` and the app's `spacemouse_status` command. No file system, shell,
  dialog, window or webview commands are exposed to the page.
* **The server** binds `127.0.0.1` only, runs without auth (single local user) and without a queue.
  Its shutdown route is token-protected and loopback/host-checked (see
  `src/ada/comms/rest/local_shutdown.py`). Like any local web server it is reachable by other programs
  of the same user; it is not reachable from other machines.

## Build

```bash
pixi run -e desktop-shell build-desktop     # -> src/desktop/target/release/ada-desktop.exe
```

The `desktop-shell` pixi environment (win-64) has Rust from conda-forge; linking uses the MSVC
toolchain (Visual Studio Build Tools), as for any Rust build on Windows. A plain
`cargo build --release --locked` in this directory works too. Set `CARGO_TARGET_DIR` to build outside
the checkout; `target/`, `gen/` and `permissions/autogenerated/` are build output and ignored.

The release profile optimises for size (`opt-level = "z"`, LTO, one codegen unit, stripped,
`panic = "abort"`): the exe is about 3.5 MB. `Cargo.lock` is committed and the build uses `--locked`.
WebView2 itself is not bundled: it ships with Windows 11 and current Windows 10; when it is missing the
shell shows a message box pointing at the Evergreen runtime download and exits with code 3.

## Developing the app

```
pixi run desktop            # Windows; the viewer, the frontend and the shell, all from this checkout
```

This runs `ada desktop --dev` (in the `desktop-dev` environment, which has the REST stack) after its
task dependencies: `winstall` and `wasm-adacpp-current` in the `frontend` environment (npm install,
the wasm backend) and `build-desktop` in `desktop-shell` (`cargo build`, incremental, so a no-op when the
shell is up to date; the first build takes a couple of minutes). Then, step by step:

1. starts the viewer as `uvicorn ada.comms.rest.app:app --reload` on the usual per-user port (local
   storage, no login), watching `src/ada`;
2. starts Vite from `src/frontend` with `vite.config.desktop.ts` (the regular config plus a proxy of
   `/api` including websockets, `/config.js` and `/healthz` to the viewer; `ADA_DESKTOP_BACKEND` carries
   the viewer URL) on the viewer port + 500 (`--dev-port N` to change), waiting for it to answer;
3. starts `ada-desktop --url http://127.0.0.1:<vite port>/` from `src/desktop/target/release` (or
   `--binary` / `$ADA_DESKTOP_BINARY`);
4. when the window closes (or on Ctrl+C), ends both servers and everything they started.

What reloads: **frontend** edits hot-reload in the window (Vite HMR); **Python** edits under `src/ada`
restart the viewer in a second or two (the page keeps working and re-requests); the **shell** does not
reload, re-run the task after changing `src/desktop` (it rebuilds). The window's origin is Vite's, so
browser-stored layouts and settings are separate from a normal `ada desktop` run.

`--dev` does not use the shutdown route, so the viewer is stopped by killing its process tree. To use a
Vite you already run, start it with `ADA_DESKTOP_BACKEND=http://127.0.0.1:PORT/` and `--config
vite.config.desktop.ts`, and pass `ada desktop --frontend-url http://127.0.0.1:VITEPORT/`. Windows only for
now; elsewhere the command says so.

## Packaging

The shell ships as the conda package `ada-py-desktop` (Windows only), an extra output of the ada-py
feedstock, which builds this directory with `cargo build --release --locked`, bundles the crate
licenses with `cargo-bundle-licenses`, installs `ada-desktop.exe` to `%LIBRARY_BIN%` and tests it with
`ada-desktop --version`. `ada desktop` finds it there (or on `PATH`, via `$ADA_DESKTOP_BINARY`, or
`--binary PATH`) and prints an install hint when it is missing.

## Limitations

* Windows only so far; macOS/Linux would need their HID backends enabled and testing (WebKit there
  has no WebGPU or WebHID in most versions).
* The viewer runs without a job queue: conversions that need a worker are unavailable, as in any
  local viewer without one. Files the page can read itself (glTF/GLB, and what the in-browser
  converters handle) open fine.
* FILE is uploaded (a copy in the local storage), not opened in place; uploads are subject to the
  server's direct-upload size limit.
* `--native-drop` only logs the dropped paths; nothing consumes them yet.
* Pop-out windows each have their own `sessionStorage` (as separate browser windows would after the
  first navigation).
* No installer, auto-update or code signing; the conda package is the distribution.
