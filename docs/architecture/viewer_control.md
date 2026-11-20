# Viewer control API

An agent -- a script, or later an MCP server that wraps these routes one tool per route or command --
can **look at** what the clash pipeline stored and **drive** the viewer a user has open: focus a
joint, select objects by name, move the camera, load a detail model, and read back what the viewer
shows so it can confirm. Everything is under `/api` and behind the same authentication as the rest
of the REST API (`auth.current_user`); scoped routes take the scope in the path as everywhere else
(`user:me`, `project:<id>`, `shared`).

Code: `src/ada/comms/rest/viewer_control.py` (vocabulary and session hub),
`routes/viewer_control.py`, `clash_inspect.py` (read-only clash views), and on the browser side
`src/frontend/src/services/viewerControl/`.

## Off by default

Two switches: the server's, and each tab's.

**The server**: `ADA_VIEWER_CONTROL` (`Settings.viewer_control`), one of three modes. On a Helm
deployment, set it through the chart's `extraEnv`.

| `ADA_VIEWER_CONTROL` | Mode | What happens |
|---|---|---|
| unset, `false`, `off` | `off` (the default) | Every `/api/viewer/...` route answers `404` ("viewer control is disabled on this server ..."), no socket is accepted, and the page shows no switch. |
| `true`, `opt-in` | `opt-in` | On; each tab's switch starts **off**: a user turns it on for the tab they want driven. For a deployed server. |
| `on`, `auto` | `auto` | On; each tab's switch starts **on**, so every tab takes part without being touched. For a local dev stack. |

Any other value stops the server from starting (a typo must not turn on a way to drive viewers). The
page learns the mode from `/config.js` (`window.ADA_VIEWER_CONTROL = "opt-in"` or `"auto"`, absent when
off) and `/api/config` (`viewerControl: "off" | "opt-in" | "auto"`). The read-only clash routes below
do not depend on it.

**The tab**: Settings > *External controls* > "Let an agent drive this tab", offered only on a server
that allows it. Per tab (kept in the tab's `sessionStorage`): it starts as the server's mode says, and
once the user switches it, their choice holds for that tab until it is closed -- a tab switched off on
an `auto` server stays off. While it is off the tab asks for no ticket and opens no socket; switching
it off closes the socket. (The desktop viewer offers the same switch, off until turned on; there it
decides whether the tab answers a local client's commands.)

## Whose viewer

A viewer tab opens a **session**. A session belongs to the user who opened it, and every lookup is
by (user, session id): another user's session id answers `404` exactly as an unknown one does, so no
user can see, drive or answer for another user's viewer.

**Several tabs.** Each open tab is one session. A command goes to the session named in
`session_id`, or -- when none is named -- to *every* live session of the caller; each tab answers
for itself. `GET /api/viewer/sessions` lists them, most recently heard from first, so an agent can
pick one.

**Transport: the websocket envelope.** Agents talk REST (below); the server relays to the tab over a
websocket carrying the same FlatBuffer `Message` the desktop viewer and its websocket server use
(`src/flatbuffers/schemas`), with three verbs (`ada.comms.viewer_wire`):

| `command_type` | Direction | `request_id` | `payload_json` |
|---|---|---|---|
| `VIEWER_COMMAND` (28) | to the viewer | the command id | `{"command": {...}}` or `{"commands": [...]}` |
| `VIEWER_ANSWER` (29) | from the viewer | echoed | `{ok, error, result, steps?, state}` |
| `VIEWER_STATE` (30) | from the viewer | -- | `ViewerState`, when the tab opens and when what it shows changes |

* **Hosted (REST) viewer.** The tab asks `POST /api/viewer/ws-ticket` (authenticated like every
  route; body `{client?}`) for a single-use ticket valid for 60 s -- a browser's websocket cannot
  send the bearer header -- and opens `GET /api/viewer/ws?ticket=...` (`wss:` on an `https:` page).
  The socket is one session of the ticket's user, for as long as it is open; it is opened again
  with a new ticket when it drops. A refused ticket, or a server with viewer control off, closes
  the handshake.
* **Desktop viewer.** It already holds a websocket to its `ada` websocket server. A local client
  (a script) sends `VIEWER_COMMAND` with `target_group` WEB and the viewer's `target_id` (from
  `LIST_WEB_CLIENTS`); the server relays it, and the viewer answers `VIEWER_ANSWER` to that client
  (`target_group` LOCAL, `target_id` the sender's instance id), or refuses while its external
  controls are off. `VIEWER_STATE` sent to the server is kept per viewer
  (`ada.comms.msg_handling.viewer_control`).

**Deployment.** The ingress in front of the API must pass websocket upgrades on `/api/viewer/ws`
and keep an idle connection open (uvicorn pings it every 20 s; an idle timeout of a minute or more
is enough).

**One process.** Sessions (open sockets) and commands' answers live in the API process's memory: the API runs as
one replica. A scaled-out API would need the hub moved onto NATS, as the job queue is.

## Driving the viewer

| Route | Body / query | Answer |
|---|---|---|
| `GET /api/viewer/sessions` | | `{sessions: [Session]}` |
| `GET /api/viewer/sessions/{session_id}` | | `Session` |
| `POST /api/viewer/commands` | `{command \| commands, session_id?, wait_s?}` | `CommandOutcome` |
| `GET /api/viewer/commands/{command_id}` | `wait_s?` | `CommandOutcome` |
| `GET /api/viewer/commands/schema` | | that body as JSON schema (each action a branch in `$defs`) |

`POST /api/viewer/commands` takes one `command`, or `commands`: a **batch** of up to 20 that each tab
runs in order -- `open_detail_model`, then `focus_joint`, then `camera`, say -- stopping at the first
that fails. It validates them (`400` with the reasons for anything outside the vocabulary, or for both
or neither of `command` / `commands`), sends them down the tabs' sockets, and waits up to `wait_s` seconds (default 10, at
most 60; `0` returns at once) for the tabs to answer. `404` when the caller has no such, or no,
viewer open.

A batch's answer per tab has `steps`, one per command in order:
`{index, action, status: "done" | "error" | "skipped", error, result}`. The tab's `status` is `done`
when every step was, else `error`, its `error` naming the command that failed
("command 2 of 3 (focus_joint) failed: ..."); the commands after it are `skipped`.

```text
Session        = {session_id, created_at, last_seen, idle_s, client: {title, path},
                  state: ViewerState, state_at}
CommandOutcome = {command_id, command | commands, issued_at,
                  results: [{session_id, status, error, result, state, answered_at, steps?}]}
                 status: "done" | "error" | "pending" (not answered yet) | "gone" (tab closed)
ViewerState    = {scope, viewport: "three" | "native" | null,
                  loaded_models: [source name], active_model,
                  detail_models_loaded: [detail_id],
                  selection: {count, objects: [{model, id, node?}] (first 50), truncated,
                              bounds: {min, max} | null},
                  camera: {eye, target, up, fovDeg} | null,
                  clash: {run_key, joints, selected_joint}}
```

Every answer carries the `state` the command left; a tab also reports its state when the selection,
the loaded models, the scope or the open clash run change, so `GET /api/viewer/sessions` is current
without sending anything.

### Commands

One object per command, discriminated by `action`. Unknown fields are refused.

| `action` | Fields | Does | `result` |
|---|---|---|---|
| `focus_joint` | `joint_id`, `run_key?`, `scope?`, `frame` (true), `reveal_panel` (true) | What clicking a joint does: makes it the joints review's cursor, opens its group, selects its members in the loaded model(s), brings up Connections > Review, and fits the camera to them. `run_key` opens that convex-contact run first when it is not the one open. | `{joint_id, selected}` |
| `select` | `names: [str]`, `file?`, `frame` (true) | Selects the objects of those names in a loaded model (`file`, a loaded source name; default the active one) and fits the camera to them. An error when none is found. | `{file, selected, bounds}` |
| `clear_selection` | | Clears the selection. | `{selected: 0}` |
| `camera` | exactly one of `fit` (`selection` \| `all`), `view` (`top`, `bottom`, `front`, `back`, `left`, `right`, `isometric`), `eye` + `target`, `box: {min, max}` | Moves the camera. `eye`/`target` keep the world's up axis up; `box` frames a world box keeping the viewing direction. | `{camera}` |
| `open_detail_model` | `detail_id`, `scope?`, `joint_id?`, `frame` (true) | Loads a kept detail model, as its Load button does (it must be `done`). With `joint_id`: selects that joint's connection in it and frames it; a joint the model does not hold (declined, failed) is focused in the clash run the model was made from instead, so its members are still on screen. | `{detail_id, glb_key}` or `{detail_id, joint_id, shown_in: "detail_model", connections}` or `{..., shown_in: "source_model", run_key, selected}` |
| `find_objects` | `names: [str]`, `file?` | Looks names up in the loaded models without changing anything (the selection is put back). | `{found: [{name, file, objects, centre, bounds}], missing: [name]}` |
| `get_state` | | Nothing; the answer's `state` is the point. | `null` |

Coordinates are model units, in the viewer's world (`up` is the model's up axis).

## Reading what was stored

Read-only, from the documents the clash pipeline already writes (a convex-contact run, a detail
model's record and take-off). Nothing is recomputed.

| Route | Query / body | Answer |
|---|---|---|
| `GET /api/scopes/{scope}/clash-contacts/latest` | `target?` (a target object as JSON: `{"kind": "file", "source_key"}`, `{"kind": "node", ...}`, `{"kind": "group", "group_id"}`) | `{run: {run_id, key, target, label, created_at, contacts, members, ...}}` -- the newest run of that target, else of the scope; `404` for none |
| `GET /api/scopes/{scope}/clash-contacts/joints` | `key` (a run key, or `latest`), `target?`, `ids?` (comma-separated), `member?` (a member name), `limit?` (100, at most 500) | `{run: {key, run_id, label, target, created_at, contacts}, joints: [Joint], missing: [id], truncated}` |
| `GET /api/scopes/{scope}/clash-detail/models` | | the scope's detail models (existing route) |
| `GET /api/scopes/{scope}/clash-detail/models/{detail_id}` | `joint_ids?` (comma-separated; default the joints it was asked to build) | `DetailView` |
| `POST /api/scopes/{scope}/clash-contacts/detail` | `{run_key, contact_ids, spec, options?, target?}` -- `run_key: "latest"` details the newest run (of `target` when given) | `{job_id, detail_id, glb_key, derived_key, run_key}`; poll `GET /api/convert/{job_id}` |

```text
Joint      = {id, members: [{index, id, name, role: "incoming" | "landing" | null, element?, part?}],
              centre: [x, y, z], normal, distance, contact_area,
              applicable: [{spec, capability, priority, tags?}] (highest priority first)}
DetailView = {model: <record: detail_id, status, spec, source, joint_ids, counts, glb_key, ...>,
              takeoff, built_by, has_connection_records, run_found,
              joints: [{joint_id,
                        status: "built" | "declined" | "failed" | "pending" | "not_requested" | "unknown",
                        connections: [{joint_id, connection, spec, member_roles, centre, plates,
                                       welds: [{name, weld_type, throat, sided, length, members, metadata}]}],
                        not_built: {joint_id, kind, reason, spec?, declined: [{spec, reason}]} | null,
                        weld_failures: [{joint_id, connection, place, weld_type, reason}],
                        contact: Joint | null}]}
```

`connections` and their welds come from the take-off's `connections` records
(`ada.clash.detail.connection_records`); a detail model built before those were written has
`has_connection_records: false` and only the take-off's counts. A weld's `metadata` is whatever
scalar metadata its builder gave it (where a builder names the seam's place, side or corner, it is
there). `contact` is read from the run the model was detailed from while that run is stored
(`run_found`).

## Example: a declined joint

A joint was declined, so it is in no detail model's GLB. The agent finds it, says which members it
is, and shows them to the user in the source model:

```text
GET  /api/scopes/user:me/clash-detail/models
     -> the newest model, detail_id "20260101T120000000Z-0123abcd"
GET  /api/scopes/user:me/clash-detail/models/20260101T120000000Z-0123abcd?joint_ids=0123456789ab
     -> joints[0].status "declined", not_built.reason "takes an incoming at 60 to 120 degrees; ...",
        contact.members [column_1 (landing), brace_1 (incoming)], contact.centre [x, y, z]
POST /api/viewer/commands
     {"command": {"action": "open_detail_model",
                  "detail_id": "20260101T120000000Z-0123abcd", "joint_id": "0123456789ab"}}
     -> results[0].result.shown_in "source_model": the run was opened, the joint focused and framed
```

Or, without a detail model: `GET .../clash-contacts/joints?key=latest&member=brace_1`, then
`{"action": "focus_joint", "joint_id": "...", "run_key": "<run.key>"}`, or
`{"action": "select", "names": ["column_1", "brace_1"]}`.

And to re-run a detail for a subset of a run's joints:

```text
POST /api/scopes/user:me/clash-contacts/detail
     {"run_key": "latest", "contact_ids": ["0123456789ab"], "spec": "<spec name>"}
     -> {job_id, detail_id, run_key, ...}
GET  /api/convert/{job_id}            (until done)
POST /api/viewer/commands  {"command": {"action": "open_detail_model", "detail_id": "...",
                                        "joint_id": "0123456789ab"}}
```

## From the command line

`ada viewer` (`ada_cli/viewer.py`; the full reference is in the [CLI page](../cli.md)) wraps all of
the above: `sessions`, `state`, `focus-joint`, `select`, `clear-selection`, `camera`, `open-detail`,
`find`, `run` (a command or a batch from JSON, the same body as `POST /api/viewer/commands`),
`schema`, and the read-outs `joints` and `detail`.

**Where it goes.** `--via auto` (the default) talks to a hosted viewer's REST API when a viewer URL
is configured -- `--url`, or `ADAPY_API_BASE` (`ADAPY_BASE_URL`, `ADAPY_VIEWER_URL`) with the token
in `ADAPY_API_TOKEN`, as every remote `ada` command reads them -- and otherwise to the desktop
viewer's websocket server (`--ws-host` / `--ws-port`, default `localhost:8765`): there it lists the
viewers (`LIST_WEB_CLIENTS`; a session is a viewer's instance id), sends `VIEWER_COMMAND` to them
and waits for each `VIEWER_ANSWER` by request id. `--via remote` / `--via local` force one. `joints`
and `detail` read stored documents and need the hosted server.

A short summary is printed by default, `--json` prints the raw answer. The exit code is 0 when every
targeted tab answered `done`, 1 when the server refused (no viewer open, viewer control disabled --
its message is printed) or any tab answered `error`, closed or did not answer within `--wait`, and 2
for a wrong invocation.

```bash
ada viewer sessions
ada viewer focus-joint 0123456789ab --run-key <run key>
ada viewer camera --eye 10,10,10 --target 0,0,0
ada viewer run batch.json --session <session id> --wait 30
ada viewer joints --run latest --member brace_1
```
## As tools

Each read route and each command is meant to become one tool of an MCP server wrapping this API:
`list_viewer_sessions`, `viewer_focus_joint`, `viewer_select`, `viewer_clear_selection`,
`viewer_camera`, `viewer_open_detail_model`, `viewer_find_objects`, `viewer_get_state` (each a
`POST /api/viewer/commands` with that `action`; their input schemas are the branches of
`GET /api/viewer/commands/schema`), and `latest_clash_run`, `clash_joints`, `detail_model`,
`detail_from_run` for the routes above.
