I tried to reproduce this on current `develop` (7a027f757, 2026-11-17), built locally and rendered in headless Chromium with `htmlLabels` both `true` and `false`.

I could not see the literal `**` / `_` markers. All three sources from the report render with the markers consumed:

- `"This **is** _Markdown_"` -> `This`, bold `is`, italic `Markdown`
- `p["**Person** _record_"]` and `q["Order **no. 1**"]` -> markers gone
- relationship label `"**places** an _order_"` -> markers gone
- the `an-er-diagram-with-unicode-text-without-htmllabels-default.mmd` fixture -> markers gone in the entity name, types, names and comment

This matches the code on `develop`: `erDb.ts` sets `labelType: 'markdown'` for entities and edges (since #7276), and both `edges.js` and `erBox.ts` call `createText` with markdown enabled.

One thing I noticed: `develop`'s `packages/mermaid/package.json` says `12.3.0`, not `13.0.0-alpha.1`. So I suspect the bug is on a build or branch that is not `develop`.

@reporter, could you say which commit or branch your `13.0.0-alpha.1` build came from? If it is a published npm tag, I can test that exact version.

Separately, the fixture's bold-italic parts (`***The l😀icense #***`, `*__List~List~int~~sdfds__*`) do not render as bold italic on `develop`: nested emphasis keeps only one style. That is the problem in #8500, which already has a fix in #8502, so I did not open a new issue for it.
