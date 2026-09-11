# README assets

Regenerate all light/dark and desktop/mobile SVGs offline:

```bash
uv run --no-project --with matplotlib scripts/readme-charts.py
```

No provider requests are made. The command needs local run artifacts and the
Matplotlib Python package. After adding runs, update the README's summary numbers
and exact-results table to match the new snapshot.

SVG text is embedded as paths for predictable rendering on GitHub. `<picture>`
selects the appropriate theme and viewport size; light desktop SVGs are the fallback.
Image descriptions and the text table expose the main results without the charts.
