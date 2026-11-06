# Layout snapshot comparison

Checks that a frontend change leaves the classic UI pixel- and DOM-identical to `main`.
These are scripts, not pytest tests; run them with `pixi run -e tests python <script>` (Playwright and Pillow).

1. Build `main` and the branch (`npm run build` in `src/frontend`) into two dist directories.
2. Serve each one against a running viewer API on :8080:
   `python serve_dist.py <dist> <port> [upstream]`.
3. Capture:
   `python capture.py <out> main=http://127.0.0.1:5175 branch=http://127.0.0.1:5176 [--model=<name>]`.
   `--model` names a stored model with a procedural cellbuilder; without it the cellbuilder states are skipped.
   `@<query>` after a URL appends query parameters for that target.
4. Compare: `python compare.py <out> main branch` (pixel tolerance 0, exact DOM after normalising build ids).

The classic UI is rendered by the layout engine only (the hand-written tree and its `?layout=classic` switch are
gone), so there is one target per build. Known noise: `mobile_*` states can show 1-line pixel differences that also
appear between two captures of the same build.
