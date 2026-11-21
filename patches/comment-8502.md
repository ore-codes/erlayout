I tested this against current `develop` (7a027f757, 2026-11-17).

- It merges cleanly. `createText.spec.ts`, `handle-markdown-text.spec.ts` and `splitText.spec.ts` pass (65 passed, 1 skipped).
- I built the merge and rendered ER diagrams in headless Chromium. `***Both***` and `_**testẽ**_` now render bold and italic with `htmlLabels: false`. With `htmlLabels: true` they already did.
- `splitText`/width measurement goes through `updateTextContentAndStyles`, so `strongEm` is measured with both styles as well.

Unrelated to this PR, but found while testing: markdown on an ER attribute type that uses generics (`**List~int~**`) loses its styling today, regardless of this change. The cause is in `erBox.ts`, not `markdownToLines`, and I'm reporting it separately: <link to new issue>.
