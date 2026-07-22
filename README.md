# mindmap-creator

An [Open Notebook](https://open-notebook.ai) **creator** plugin: turns notebook
content into a [mermaid](https://mermaid.js.org/) mindmap.

- Emits the `mindmap.v1` artifact schema, rendered by the shipped view bundle.
- Attaches downloadable exports to every result: Markdown (title, description, and
  the mermaid source), SVG, and PNG — surfaced as download buttons in the UI.
- Implements the [`open-notebook-creator-sdk`](https://github.com/Notebooker-ai/open-notebook-creator-sdk) `BaseCreator` contract; registers under `open_notebook.creators`.

## Model roles

| role | kind | requires |
|------|------|----------|
| `text` | language | `structured_json` |

## Config

| field | default | notes |
|-------|---------|-------|
| `max_depth` | 5 | 2–8 hierarchy depth |
| `count` | 1 | 1–6 variants to generate (the host runs one generation per variant) |

## Output

`mindmap.v1` payload:

```json
{
  "title": "...",
  "mermaid_syntax": "mindmap\n  root((...))\n    ...",
  "description": "..."
}
```

Each result also attaches three export files (named after the slugified title):
`{slug}.md` (`text/markdown`), `{slug}.svg` (`image/svg+xml`), and `{slug}.png`
(`image/png`). SVG/PNG are rendered by a pure-Python port of the view bundle's
mindmap renderer; an export failure downgrades to a warning, never a failed
generation.

## Dev

```bash
uv sync --extra dev
uv run pytest
```

MIT licensed.
