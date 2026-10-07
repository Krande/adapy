# Docs pipeline

This site is built with [Zensical](https://zensical.org), the static site generator from
the Material for MkDocs team. It is configured by `zensical.toml` at the repository root.
The pages are Markdown in `docs/`.

```mermaid
flowchart TB
    subgraph src["Sources"]
        MD["docs/**/*.md"]
        NB["docs/**/*.ipynb"]
        VER["verification/<br/>paradoc project"]
        PYSRC["src/ada<br/>docstrings"]
        STATIC["docs/_static/<br/>figures · profiling app · extra.css"]
    end

    subgraph steps["pixi run -e docs docs"]
        FEADOC["fea-doc<br/>paradoc build verification"]
        NBCONV["docs-notebooks<br/>scripts/docs_notebooks.py --execute"]
        ZEN["docs-site<br/>zensical build --clean"]
    end

    REPORT["docs/_static/fea-report/<br/>(gitignored)"]
    NBMD["NAME.md + NAME_files/viewer-N.html<br/>beside each notebook (gitignored)"]
    BUNDLE["docs/_static/notebook-viewer/<br/>viewer-HASH.js / .css (gitignored)"]
    SITE[("site/")]

    VER --> FEADOC --> REPORT
    NB --> NBCONV --> NBMD
    NBCONV --> BUNDLE
    MD & NBMD & STATIC & REPORT & BUNDLE --> ZEN
    PYSRC -- "mkdocstrings (::: ada)" --> ZEN
    ZEN --> SITE
    SITE --> GH["GitHub Pages<br/>ci-pages.yml"]
    SITE --> IMG["docs image (nginx)<br/>deploy/Dockerfile.docs"]
```

## Tasks

| Task (`pixi run -e docs …`) | Does |
|---|---|
| `docs` | Everything: `fea-doc`, then `docs-site`. |
| `docs-site` | `docs-notebooks`, then `zensical build --clean` into `site/`. Reuses whatever `docs/_static/fea-report/` already holds. |
| `docs-notebooks` | Executes and converts the notebooks (below). |
| `docs-serve` | `zensical serve`: live-reloading preview on <http://localhost:8000>. Run `docs-notebooks` once first. |
| `serve` | Builds everything, then serves `site/` with `scripts/docs_serve.py` on :8080. |
| `fea-doc`, `fea-doc-cached`, `fea-doc-{docx,odt,pdf}` | The FEA verification report and its downloadable files. |

## Notebooks

Zensical runs no MkDocs plugins, so there is no nbsphinx or mkdocs-jupyter step.
`scripts/docs_notebooks.py` fills the gap:

```mermaid
sequenceDiagram
    autonumber
    participant S as docs_notebooks.py
    participant K as Jupyter kernel (nbclient)
    participant X as nbconvert MarkdownExporter
    participant D as docs/

    loop each docs/**/*.ipynb
        S->>K: execute (cwd = notebook folder)
        K-->>S: outputs, where obj.show() gives a text/html iframe srcdoc (viewer + GLB)
        S->>S: unescape each srcdoc
        S->>D: _static/notebook-viewer/viewer-HASH.js / .css (shared, once)
        S->>D: NAME_files/viewer-N.html (model + reference to the shared bundle)
        S->>S: replace the output with a lazy iframe src=NAME_files/viewer-N.html
        S->>X: export Markdown
        S->>D: NAME.md (generated marker, download link to NAME.ipynb)
    end
```

- **The 3D viewers stay fully interactive.** A `.show()` output is plain HTML, not an
  ipywidget, so it needs no kernel or widget state after the build.
- **Pages stay light.** Each viewer is a separate, lazily loaded page of a few KB plus the
  model. The ~3 MB viewer app is one content-hashed file shared by all of them and cached
  by the browser.
- **The source notebook is published next to its page** and linked for download.
- A cell that raises fails the build. `--no-execute` converts stored outputs only, and
  `--clean` removes everything the script generated.

## Configuration notes

- `use_directory_urls = false` keeps the Sphinx-era URLs (`documents/cli.html`,
  `_static/fea-report/index.html`) working for existing links.
- Mermaid diagrams are `pymdownx.superfences` custom fences: write a ```` ```mermaid ````
  block. GitHub renders the same blocks when you read the Markdown in the repository.
- The API reference (`documents/code.md`) uses mkdocstrings with the Python handler
  (`paths = ["src"]`, Sphinx-style docstrings).
