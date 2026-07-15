"""Startup smoke test for a BUILT viewer image — run it before the image is pushed.

Piped into the image's interpreter by the viewer-build jobs (.forgejo/workflows/build.yaml and
.github/workflows/publish-image.yaml); it is not part of the shipped source and never runs in the
test suite.

Why this exists: the viewer runtime ships a CURATED subset of ada (see the COPY block in
deploy/Dockerfile.viewer), not the whole package. Nothing in the ordinary test suite imports the
server the way that trimmed image does — the tests, and the worker image, both have all of src/ada
on the path — so a module the REST app imports at startup can be missing from the image while every
check stays green. That is not hypothetical: adapy 0.30.0 shipped a viewer that crashlooped on
`ModuleNotFoundError: No module named 'ada.cad'`, because the published tessellator vocabulary
became DISCOVERED (imported at module scope) rather than listed, and ada.cad was not in the subset.
The worker image built from the very same commit was fine, which is exactly what hid it.

So: import the app the way the container does, inside the container, and refuse to push if that
fails. Keep this cheap and startup-only — it is a packaging gate, not a functional test.
"""

from __future__ import annotations

import pathlib
import sys

# The REST app builds its object store at import time (create_app -> Storage.from_settings), and the
# default local store canonicalizes ADA_VIEWER_LOCAL_PATH — /data, a volume in the deployment but an
# absent path in a bare `docker run`. Create it so a missing mount reads as what it is (an unset
# environment) instead of a fake failure of this gate.
pathlib.Path("/data").mkdir(parents=True, exist_ok=True)

# The import under test: this is the line that crashlooped 0.30.0. It transitively runs the
# module-level registration (_register_ada_loadable and friends) where the discovery imports live.
import ada.comms.rest.app  # noqa: E402
from ada.comms.rest.converter import _step_glb_pipelines  # noqa: E402

vocabulary = list(_step_glb_pipelines())

# A vocabulary that imports but comes back empty would leave the frontend with an unselectable
# engine dropdown — a silent degrade rather than a crash, so assert it rather than trusting the
# import alone. The API's own list is the static base; adacpp tracks are unioned in from the workers
# that actually run them, so this stays true in the slim image where no adacpp is installed.
if not vocabulary:
    sys.exit("viewer smoke FAILED: published step_glb_pipeline vocabulary is empty")

assert ada.comms.rest.app.app is not None, "viewer smoke FAILED: create_app() produced no ASGI app"

print(f"viewer smoke OK: app imported; step_glb_pipeline vocabulary = {vocabulary}")
