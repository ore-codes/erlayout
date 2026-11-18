import sys

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import Client, StdioServerParameters  # noqa: E402

from erlayout.mcp_server import MAX_INLINE, _safe_path, build_server, run_layout  # noqa: E402

SCHEMA = """
CREATE TABLE a (id INT PRIMARY KEY);
CREATE TABLE b (id INT PRIMARY KEY, a_id INT, FOREIGN KEY (a_id) REFERENCES a(id));
CREATE TABLE c (id INT PRIMARY KEY, a_id INT, b_id INT,
  FOREIGN KEY (a_id) REFERENCES a(id), FOREIGN KEY (b_id) REFERENCES b(id));
"""


def call(server_or_params, name, args):
    async def go():
        async with Client(server_or_params) as client:
            return await client.call_tool(name, args)
    return anyio.run(go)


def text(result):
    return "".join(c.text for c in result.content if c.type == "text")


def test_layout_summary_and_inline_mermaid():
    summary, diagram = run_layout(SCHEMA, None, "mermaid", 10, False, 2)
    assert summary["tables"] == 3 and summary["foreign_keys"] == 3
    assert summary["crossings"] == 0 and summary["proven_optimal"] and summary["guarantee"]
    assert summary["independent_checks"]["crossings"] == summary["crossings"]
    assert "erDiagram" in diagram


def test_parse_error_is_reported_not_raised_as_a_crash():
    with pytest.raises(ValueError, match="could not parse schema|no tables found"):
        run_layout("this is not a schema", None, "svg", 5, False, 2)


def test_save_as_stays_inside_the_output_directory(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert _safe_path(out, "er.svg", "svg", False) == (out / "er.svg").resolve()
    assert _safe_path(out, "docs/er.svg", "svg", False) == (out / "docs" / "er.svg").resolve()
    for bad in ("/etc/er.svg", "../er.svg", "a/../../er.svg", "er.png", "er.drawio"):
        with pytest.raises(ValueError):
            _safe_path(out, bad, "svg", False)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (out / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="inside the output directory"):
        _safe_path(out, "link/er.svg", "svg", False)


def test_existing_file_is_not_overwritten_unless_asked(tmp_path):
    (tmp_path / "er.svg").write_text("mine")
    with pytest.raises(ValueError, match="already exists"):
        _safe_path(tmp_path, "er.svg", "svg", False)
    assert _safe_path(tmp_path, "er.svg", "svg", True)


def test_tool_over_the_protocol(tmp_path):
    server = build_server(tmp_path, max_time=10, workers=2)

    async def go():
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            saved = await client.call_tool("layout_er_diagram", {"schema": SCHEMA, "save_as": "docs/er.svg"})
            inline = await client.call_tool("layout_er_diagram", {"schema": SCHEMA, "format": "mermaid"})
            escape = await client.call_tool("layout_er_diagram", {"schema": SCHEMA, "save_as": "../x.svg"})
            return tools, saved, inline, escape
    tools, saved, inline, escape = anyio.run(go)

    assert [t.name for t in tools] == ["layout_er_diagram"]
    assert not saved.is_error and (tmp_path / "docs" / "er.svg").read_text().startswith("<svg")
    assert "saved_to" in text(saved) and "<svg" not in text(saved)
    assert not inline.is_error and "erDiagram" in text(inline)
    assert escape.is_error and "relative path" in text(escape)
    assert not (tmp_path.parent / "x.svg").exists()


def test_large_diagram_must_be_saved(tmp_path, monkeypatch):
    import erlayout.mcp_server as m
    monkeypatch.setattr(m, "MAX_INLINE", 100)
    r = call(build_server(tmp_path, 10, 2), "layout_er_diagram", {"schema": SCHEMA})
    assert r.is_error and "pass save_as" in text(r)
    assert MAX_INLINE == 20_000


def test_time_limit_is_capped_by_the_server(tmp_path, monkeypatch):
    import erlayout.mcp_server as m
    seen = []
    real = m.run_layout
    monkeypatch.setattr(m, "run_layout", lambda *a: seen.append(a[3]) or real(*a))
    call(build_server(tmp_path, max_time=7, workers=2), "layout_er_diagram",
         {"schema": SCHEMA, "format": "mermaid", "time_limit": 900})
    assert seen == [7]


def test_stdio_server_starts_and_answers(tmp_path):
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "erlayout.cli", "mcp", "--output-dir", str(tmp_path), "--max-time", "10"])
    r = call(params, "layout_er_diagram", {"schema": SCHEMA, "format": "mermaid", "time_limit": 5})
    assert not r.is_error and "proven_optimal" in text(r)
