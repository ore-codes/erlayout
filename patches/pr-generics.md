## :bookmark_tabs: Summary

Markdown formatting on an ER attribute type that uses the `~` generic syntax was dropped: `**List~int~**` rendered as plain `List<int>`, with `htmlLabels` on or off. Types without generics were fine.

Resolves #<new issue number>
Related: #8716, #8502

## :straight_ruler: Design Decisions

`addText` in `erBox.ts` escapes `<`/`>` before calling `createText` and restores them afterwards by assigning `textContent` on the first child and each first descendant. Assigning `textContent` replaces all child nodes, so the formatting elements markdown produced were removed. The restore step now walks the text nodes and only rewrites their values, leaving the element structure intact.

Tests (`erBox.spec.ts`): a generic type is written with angle brackets; `**List~int~**` stays bold; `**int**` stays bold (control). The two generic-type tests fail on `develop` and pass with this change. I did not add an e2e/visual test.

### :clipboard: Tasks

Make sure you

- [x] :book: have read the [contribution guidelines](https://mermaid.js.org/community/contributing.html)
- [x] :computer: have added necessary unit/e2e tests.
- [ ] :notebook: have added documentation. Make sure [`MERMAID_RELEASE_VERSION`](https://mermaid.js.org/community/contributing.html#update-documentation) is used for all new features.
- [x] :butterfly: If your PR makes a change that should be noted in one or more packages' changelogs, generate a changeset by running `pnpm changeset` and following the prompts. Changesets that add features should be `minor` and those that fix bugs should be `patch`. Please prefix changeset messages with `feat:`, `fix:`, or `chore:`.
