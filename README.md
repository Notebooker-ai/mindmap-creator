# mindmap-creator

An [Open Notebook](https://open-notebook.ai) **creator** plugin: turns notebook
content into a [mermaid](https://mermaid.js.org/) mindmap.

- Emits the `mindmap.v1` artifact schema (rendered client-side by `mermaid`).
- Exportable from the UI as Markdown (the mermaid source), PNG, or SVG.
- Implements the [`open-notebook-creator-sdk`](https://github.com/Notebooker-ai/open-notebook-creator-sdk) `BaseCreator` contract; registers under `open_notebook.creators`.

## Model roles

| role | kind | requires |
|------|------|----------|
| `text` | language | `structured_json` |

## Config

| field | default | notes |
|-------|---------|-------|
| `max_depth` | 5 | 2–8 hierarchy depth |

## Output

`mindmap.v1` payload:

```json
{
  "title": "...",
  "mermaid_syntax": "mindmap\n  root((...))\n    ...",
  "description": "..."
}
```

## Dev

```bash
uv sync --extra dev
uv run pytest
```

MIT licensed.
