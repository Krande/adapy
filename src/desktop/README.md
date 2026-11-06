# ada-desktop

A native window for the adapy local viewer (Ada Studio), or for a hosted deployment. It is a small
[Tauri 2](https://v2.tauri.app/) app that embeds **no** frontend: it opens a window on a viewer that is
already running, normally the one on loopback that `ada desktop` starts, or a server named with
`--server` (see [Connecting to a server](#connecting-to-a-server)). Everything the viewer does still happens in the page and the
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
| `--url URL` | The viewer to open. `http` on `127.0.0.1`, `localhost` or `[::1]`, unless `--trust-origin` names a server. |
| `--trust-origin O` | Open a hosted server: `O` is its origin (`https://host[:port]`), the only one windows show and the only one granted native events. `--url` must be on it. |
| `--auth-origin O` | An identity provider's sign-in origin windows may navigate to (no native events). Repeatable. |
| `--insecure` | Accept plain `http` for `--trust-origin` / `--auth-origin` on a non-loopback host (lab setups). |
| `--no-sso` | Do not sign in with the Windows account (see "Single sign-on" below); `ada desktop --no-sso` passes it on. |
| `--title T` | Main window title (default `Ada Studio`). Pop-outs take their page's title. |
| `--native-drop` | The shell takes dropped files (native paths, logged only for now) instead of the page. By default the page gets `File` objects, as in a browser. |
| `--no-spacemouse` | Do not read the 3D mouse natively; the shim is not injected and the page sees WebView2's own WebHID. |
| `--spacemouse-log` | No window: list the 3D mouse HID interfaces and print what the device sends. |
| `--version` | Print the version and the WebView2 version (or `not installed`) and exit 0. Needs no display and no WebView2. |

`ada desktop --shell-arg=--no-spacemouse` passes a flag through.

## Connecting to a server

The window can also open a hosted (for example on-premise) Ada Studio deployment, so its storage,
conversions, jobs and sign-in are used instead of the local viewer's:

```bash
ada desktop --server https://studio.example.com     # a URL ...
ada desktop servers add work https://studio.example.com
ada desktop --server work                             # ... or a saved name
ada desktop servers list                              # name, URL
ada desktop servers remove work
ADA_DESKTOP_SERVER=work ada desktop                   # default server; `--local` overrides it
```

Remote mode starts **no** local viewer. The shell loads the server's own URL, so the page runs on the
server's origin exactly as in a browser tab: the API is same-origin (no CORS, no API base to
configure) and sign-in works unchanged, because the OIDC redirect URI the page uses
(`origin + /auth/callback`) is the one already registered for the hosted app. Local files are opened
by the page itself (File menu), and uploads go to the server. Layouts and settings live in the page's
origin storage, so each server has its own. FILE and the local-viewer flags (`--port`, `--data`,
`--static`, `--zip`, `--viewer`) are refused with `--server`. (`--dev --server` develops the frontend
against the server, see "Developing against a hosted server".)

Profiles are a small JSON file, `%APPDATA%\ada\desktop\servers.json` on Windows
(`~/Library/Application Support/ada/desktop` on macOS, `$XDG_CONFIG_HOME/ada/desktop` elsewhere):
`{"servers": {"work": {"url": "https://studio.example.com/"}}}`, optionally with `"insecure": true` and
`"auth_origins": [...]`. `ada desktop` prints the origin it is going to trust before the window opens.

* **https is required** for any host but loopback. Plain `http` to another host needs `--insecure`
  (`servers add NAME URL --insecure` saves it): tokens then cross the network unencrypted.
* **Sign-in.** An OIDC redirect leaves the server's origin for the identity provider and comes back, in
  the same window. `ada desktop` reads `GET /api/config` (public) and allows the `auth.issuer` origin
  plus the origins of `authorization_endpoint` / `end_session_endpoint` in the issuer's
  `/.well-known/openid-configuration`, and passes them as `--auth-origin` (more with `--auth-origin
  https://sts.example.com`, repeatable, or saved with `servers add ... --auth-origin`). On top of that:
  * **Microsoft Entra ID.** When a sign-in origin is a Microsoft login host (`login.microsoftonline.com`,
    `login.microsoft.com`, `login.windows.net`, `sts.windows.net`, or a national cloud's), the shell also
    allows the hosts Microsoft's sign-in uses besides the discovered one: `login.microsoftonline.com`,
    `login.microsoft.com` (passkeys / Windows Hello, `/fido/get`), `login.windows.net`, `sts.windows.net`,
    `login.live.com`, `account.live.com` (personal accounts), `device.login.microsoftonline.com`,
    `certauth.login.microsoftonline.com` and its sub-domains (device and certificate authentication),
    `autologon.microsoftazuread-sso.com` (seamless SSO) and `mysignins.microsoft.com` (the "more
    information required" MFA registration). https on the default port only (`ENTRA_HOSTS` in
    `src/origin.rs`).
  * **Pass-through during a sign-in.** Once a window has gone from the app to a sign-in page, the
    provider may send it on to **any https page** (a federated on-premise service, an MFA provider)
    until the window is back on the trusted origin; then the allow-list applies again. So federation
    needs no `--auth-origin` per host. Plain http and other schemes stay refused.
  * These pages are navigation only: none gets IPC (the capability names the trusted origin alone).
    An allowed navigation is left to WebView2 as the page made it, so a form POST stays a POST. Only a
    refused one goes to the system browser, which can only GET it; a sign-in step that ends up there
    shows the provider's "endpoint only accepts POST requests" error (Entra: AADSTS900561) in the browser
    and never returns to the app. The shell logs every sign-in navigation (host and path, no query) on
    stderr, which tells which host that was.
  * Sign-in pop-ups (`window.open` from a sign-in page, or to a sign-in origin) open as native windows
    created by WebView2 from the original request, so `window.opener` and POST bodies survive; they
    are not saved or restored like pop-outs.
* **Pop-outs** open as native windows on the server and carry the opener's `sessionStorage` (verified on
  WebView2: `window.open` from the page copies it), so the tokens the page keeps there are present in
  the new window. The copy is taken when the window opens; the pages do not share later changes. A
  provider that rotates refresh tokens on every use could therefore invalidate one window's copy after
  another window refreshes. If that shows up, the fix is on the page side (renew silently in each
  window, or hand tokens through `BroadcastChannel`), not in the shell.
* **Single sign-on with the Windows account** (on by default, `--no-sso` turns it off). The shell
  creates the WebView2 environment itself with `AllowSingleSignOnUsingOSPrimaryAccount` set
  ([ICoreWebView2EnvironmentOptions](https://learn.microsoft.com/microsoft-edge/webview2/reference/win32/icorewebview2environmentoptions#get_allowsinglesignonusingosprimaryaccount))
  and hands it to wry (`WebviewWindowBuilder::with_environment`; wry 0.57 has no switch of its own);
  pop-outs share it. The Microsoft identity platform then signs in with the work or school account
  Windows is signed in with, and sees the device's identity (which device-based Conditional Access
  needs), as Edge does: on an Entra-joined or registered machine, "Sign in" goes to the authorize
  endpoint and straight back to `/auth/callback` without a prompt. Everything else is what wry would
  have set (its browser arguments, the UI language) in the same user data folder, so cookies and
  storage are those of earlier launches. One limit: WebView2 runs one browser per profile with one set
  of options, so a window with SSO and one without (an older ada-desktop, or `--no-sso`) cannot be open
  at the same time; the second one says so and exits. Integrated Windows authentication for intranet
  servers (Kerberos/NTLM) is WebView2's default behaviour; extra hosts can be allowed with
  `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--auth-server-allowlist=host`.

By hand: `ada-desktop --url https://studio.example.com/ --trust-origin https://studio.example.com
--auth-origin https://login.example.org`.

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
  (exactly the scheme, host and port of `--url`; for a remote server the `--trust-origin`), its `blob:`
  URLs, `about:blank`, and, for navigation only, the identity provider's sign-in pages: the
  `--auth-origin`s, the built-in Entra ID hosts when the provider is Entra, and any https page a sign-in
  passes through before it returns to the trusted origin (see "Sign-in" above). Any other navigation is
  cancelled; `http`, `https` and `mailto` links are handed to the system browser instead, anything else
  (e.g. `file:`, custom protocol handlers) is dropped. `window.open` to another site likewise opens in the
  system browser; to a sign-in page (or from one, during a sign-in) it opens a native window under the
  same rules.
* **IPC for one origin, minimal.** There is no static capability file: at startup the shell adds a
  runtime capability (`dynamic-acl`) whose remote URL is the trusted origin alone (the loopback viewer's
  `http://127.0.0.1:PORT/*`, or the server's), and which allows only `core:event:allow-listen`,
  `core:event:allow-unlisten` and the app's `spacemouse_status` command. No file system, shell, dialog,
  window or webview commands are exposed to the page, and the identity provider's pages get nothing.
  Verified against a live page: `spacemouse_status` and event listening work, `plugin:window|set_title`
  answers "not allowed by ACL".
* **Remote servers.** The shell refuses a non-loopback `--url` without `--trust-origin`, requires https
  for it unless `--insecure`, and requires the URL to be on the trusted origin. Trusting a server means
  trusting what its pages do with the 3D mouse events and nothing more; but a hostile server can still
  run any script in its own page, so only point the app at deployments you would open in a browser.
* **The local server** binds `127.0.0.1` only, runs without auth (single local user) and without a queue.
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

### Developing against a hosted server

To work on the frontend with a hosted deployment's data, storage and sign-in:

```
ada desktop servers add work https://studio.example.com    # once
pixi run desktop-server work     # just open it (builds the shell first)
pixi run desktop-backend work    # this checkout's frontend (hot reload) against it
```

`desktop-backend` runs `ada desktop --dev --backend work` (`--dev --server work` is the same thing; a plain
`--dev` stays local even when `$ADA_DESKTOP_SERVER` is set). No local viewer is started. Vite serves the
sources from `src/frontend` on **`http://localhost:<port>`** and proxies `/api` (websockets included, as
`wss` for an https server), `/healthz` and `/config.js` to the server with TLS verified, `Host` and `Origin`
presented as the server's own, and cookies and `Authorization` forwarded. `/auth/callback` and every other
route stay the SPA's. The window opens on `localhost`, not `127.0.0.1`, because the redirect URI is
registered by name.

**Register the redirect URI.** The page signs in with PKCE and sends `origin + /auth/callback`, which is now
the dev origin. The command prints the exact value; register it once in the identity provider, for the
app's *single-page application* platform, alongside the hosted app's own:

```
Register this redirect URI for the app's SPA platform: http://localhost:39198/auth/callback
```

Providers such as Microsoft Entra ID accept `http://localhost` redirect URIs for SPA apps. Until the URI is
registered (for the SPA platform, not "Web"), Entra shows AADSTS50011 ("the redirect URI ... does not match")
right after the authorize request. The port is fixed per
user (viewer port + 500, see "Port policy"; `--dev-port N` picks another, and then another URI is needed).
If it is taken the command stops instead of falling back to a free port, since the registered URI names it.

**Trust.** The shell trusts (and grants native events to) exactly `http://localhost:<port>`, the same loopback
origin as `--dev`; the hosted server is reached only through the proxy. The identity provider's origins,
read from the server's `/api/config` (`auth.issuer`) and the issuer's `.well-known`, plus `--auth-origin`
and the profile's, are allowed for navigation so the sign-in page loads in the window.

**`/config.js`.** The stock server writes a relative `window.API_BASE = "/api"`, which already goes through
the proxy. In case a deployment spells out its own address, the dev server fetches `/config.js` from the
server and rewrites that origin (and its `ws`/`wss` form) to the dev origin (`vite.desktop-proxy.mjs`);
other origins, such as the identity provider's, are left alone.

Limits: sign-in cannot work until the redirect URI is registered; layouts and settings are stored under
the dev origin, separate from the server's own; the server's CORS/CSRF rules see the server's own origin.

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
* Pop-out windows each have their own `sessionStorage`, seeded with a copy of the opener's when they
  open (see [Connecting to a server](#connecting-to-a-server) for what that means for sign-in).
* Remote mode: silent sign-in with the Windows account was verified against Microsoft Entra ID on an
  Entra-joined machine (hosted server; `--dev --backend` up to the provider's redirect-URI check); other
  providers only through the
  allow-list logic. Windows with SSO and without cannot be open at the same time (one WebView2 profile).
* No installer, auto-update or code signing; the conda package is the distribution.
