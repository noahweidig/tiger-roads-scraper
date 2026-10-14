# Contributing

This project is vibe coded with Claude Code. Contributions of any kind are welcome, AI-assisted or not.

## Setup

```
pip install -r requirements.txt
```

You also need GDAL's `ogr2ogr` on your PATH. See the README for env vars and pipeline steps.

## Pull requests

1. Open an issue first for anything larger than a small fix.
2. Fork, branch from `main`, keep changes focused.
3. Run the affected pipeline step on a small sample (one state or year) before submitting.
4. Do not commit downloaded data, generated shapefiles, logs, credentials, or local paths.
5. Describe what changed and how you tested it.

## Reporting bugs

Use the bug report template. Include the pipeline step, the year/state, and the relevant log lines.

By contributing you agree that your work is released under the [MIT License](LICENSE) and that you follow the [Code of Conduct](CODE_OF_CONDUCT.md).
