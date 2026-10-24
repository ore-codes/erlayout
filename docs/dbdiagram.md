# From dbdiagram.io to a tidy diagram

dbdiagram.io is a quick way to write a schema, but once a schema passes a few
dozen tables, the diagram often ends up as a tangle of crossing lines.
Auto-arrange does not count crossings; erlayout does. It places your tables in
columns so that as few relationship lines cross as possible, and for small and
medium schemas it proves no better arrangement exists. It then hands you the
result as a draw.io, Excalidraw or Mermaid file you can keep editing.

## 1. Copy your DBML

Open your diagram in dbdiagram.io and copy everything in the code editor on
the left (select all, copy). That text is DBML. Save it as `schema.dbml` if you
use the command line.

## 2. Lay it out

**Web interface:** run `erlayout serve`, open http://127.0.0.1:8000, paste the
DBML and press **Lay out**. The format is detected automatically. The diagram
appears within seconds and improves while the search runs.

**Command line:**

```bash
erlayout layout schema.dbml -o schema.drawio -o schema.excalidraw -o schema.mmd -o schema.svg
```

The extension of each `-o` picks the format. The files are rewritten every
time a better layout is found. To finish early, press Ctrl-C (or **Stop** in the
web interface): the best layout so far is kept and written.

### Large schemas

Above 40 tables erlayout draws one diagram per group of closely related tables
(up to 15 per group by default; `--max-group` changes this). A link between two
groups is not drawn as a line; the row says where it goes instead, for example
`→ orders (group 3)`. Tables with no drawn link are collected in one panel at
the end.

If most tables point at a few central ones (`users`, `accounts`, `stores`),
add `--hide-hubs auto`. The links to those tables become row tags as well
(`→ users (hidden hub)`), which removes the bundles of lines they would draw.
You can also name the tables: `--hide-hubs users,stores`. The web interface has
the same settings under **Options**. Groups are placed in a grid by default;
**Arrange** (`--arrange row` or `column`) puts them side by side or one above the
other instead.

## 3. Keep editing

| Format | Open it in | What you get |
| --- | --- | --- |
| `.drawio` | [app.diagrams.net](https://app.diagrams.net): *File → Open from → Device*, or the draw.io desktop app or VS Code extension | Tables, rows and curved links in their places. Each link is attached to its two rows, so it follows a table you drag. A split schema gets one page per group. |
| `.excalidraw` | [excalidraw.com](https://excalidraw.com): *Open* (opening replaces what is on the canvas, so save that first) | Tables, rows and links in their places. Each table is grouped and the links are bound to it, so dragging a table keeps its links attached. A split schema gets one frame per group. |
| `.mmd` | [mermaid.live](https://mermaid.live), or a fenced `mermaid` block in a GitHub or GitLab Markdown file | Tables and relationships only. Mermaid has no syntax for positions and always lays the diagram out itself; erlayout declares the tables in its layout order, which Mermaid often follows, but it does not keep the arrangement. |
| `.svg` | Any browser, or as an image in docs | A finished picture. |

To export the groups of a large schema separately, pick a diagram, or *Each
diagram as its own file (zip)*, under **Diagrams** in the web interface. On
the command line, add `--each-group`, which writes `schema-01-<group>.drawio`,
`schema-02-<group>.drawio` and so on next to the combined file. When a single
group is exported to Mermaid, the tables it links to in other groups are
included at the end, so its relationships still resolve. Mermaid refuses input
over 50,000 characters by default, so export a large schema to Mermaid one
group at a time.

## Going back to dbdiagram

DBML has no syntax for table positions, so you cannot paste the layout back
into dbdiagram.io. Your DBML is still the source of truth: when the schema
changes, edit it in dbdiagram and run erlayout again. Use the draw.io or
Excalidraw file for documents and reviews.

## What is read from DBML

| DBML | Used |
| --- | --- |
| `Table` with schema-qualified or quoted names (`shop.orders`, `"order items"`), aliases (`as U`) and settings | yes |
| Columns: types such as `varchar(255)`, `decimal(10, 2)`, `"timestamp with time zone"`, and enum types; `pk` and `primary key` | yes |
| Inline refs (`[ref: > users.id]`), short refs (`Ref: a.x > b.y`), long refs (`Ref name { ... }`), refs that use an alias or a schema | yes |
| Composite refs (`Ref: a.(x, y) > b.(x, y)`) | yes, one line per column pair |
| `TablePartial` and `~partial` | yes, the partial's columns are added in place |
| `<`, `>`, `-`, `<>` | `a.x > b.y` and `b.y < a.x` are drawn from `a.x` to `b.y`; one-to-one and many-to-many links are drawn from the left side to the right |
| Notes, indexes, defaults, enums' values, `TableGroup`, `Project`, sticky notes, header colours | ignored (they do not affect the layout) |

If two schemas contain a table with the same name, both tables keep their
schema prefix (`sales.users`, `auth.users`). Otherwise the prefix is dropped.

Your `TableGroup`s are not used for the split yet; erlayout groups tables by
how they reference each other.
