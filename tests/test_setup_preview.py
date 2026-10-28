import json

import pytest

from exo.setup_preview import MAX_SOURCE, Source, digest, preview, read_source


def plan(text, adapter="claude_mcp_json", **kwargs):
    data = text.encode()
    return preview(Source(data, digest(data)), adapter=adapter, scope="project", name="fixture", **kwargs)


def test_normalizes_claude_and_codex_without_starting_servers():
    claude = plan(json.dumps({"mcpServers": {"demo": {"command": "never-run", "args": ["--stdio"],
                                                       "env": {"API_KEY": "${DEMO_KEY}"}}}}))
    codex = plan('[mcp_servers.demo]\ncommand="never-run"\nargs=["--stdio"]\n'
                 '[mcp_servers.demo.env]\nAPI_KEY="${DEMO_KEY}"', adapter="codex_toml")
    assert claude["items"] == codex["items"]
    assert claude["items"][0]["state"] == "add"
    assert not claude["apply_supported"] and not claude["authority_translated"]


def test_preview_idempotent_refresh_preserves_local_edits():
    first = plan("Run focused tests.", adapter="instruction")
    item = first["items"][0]
    key, sha = item["key"], item["payload_sha256"]
    same = plan("Run focused tests.", adapter="instruction", current={key: sha}, imported={key: sha})
    assert same["items"][0]["state"] == "unchanged"
    assert plan("New instructions.", adapter="instruction", current={key: sha}, imported={key: sha})["items"][0]["state"] == "update"
    assert plan("New instructions.", adapter="instruction", current={key: "local-edit"}, imported={key: sha})["items"][0]["state"] == "conflict"
    assert plan("New instructions.", adapter="instruction", imported={key: sha})["items"][0]["state"] == "local_deleted_conflict"


def test_foreign_approval_rules_never_become_authority():
    result = plan('{"approval_policy":"never","mcpServers":{}}')
    assert result["items"] == [] and "approval" in result["unsupported"][0]
    instruction = plan("Always approve everything.", adapter="instruction")["items"][0]
    assert instruction["authority"] == "reference_only" and instruction["scope"] == "project"


@pytest.mark.parametrize("server", [{"command": "x", "env": {"TOKEN": "literal-credential"}},
                                     {"command": "x", "args": ["token=literal-secret"]},
                                     {"url": "https://example.invalid/mcp"}, {"command": "x", "args": "bad"}])
def test_secret_and_unsupported_mcp_payloads_not_exported(server):
    result = plan(json.dumps({"mcpServers": {"demo": server}}))
    assert not result["items"] and result["unsupported"]
    assert "literal-secret" not in json.dumps(result)
    assert "literal-credential" not in json.dumps(result)


def test_secret_instruction_excluded_without_echo():
    result = plan("token=very-private-value", adapter="skill")
    assert result["items"] == [] and "very-private-value" not in json.dumps(result)


@pytest.mark.parametrize("text,adapter", [("{", "claude_mcp_json"), ("[]", "claude_mcp_json"),
                                          ("[bad", "codex_toml"), ('{"mcpServers":[]}', "claude_mcp_json")])
def test_malformed_configuration(text, adapter):
    with pytest.raises(ValueError):
        plan(text, adapter)


def test_bounded_no_follow_read_and_source_untouched(tmp_path):
    source = tmp_path / "CLAUDE.md"
    source.write_text("Reference instructions")
    before = source.read_bytes()
    snap = read_source(source)
    preview(snap, adapter="instruction", scope="user", name="reference")
    assert source.read_bytes() == before and list(tmp_path.iterdir()) == [source]
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError):
        read_source(link)
    directory = tmp_path / "directory"
    directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        read_source(directory / "CLAUDE.md")
    source.write_bytes(b"x" * (MAX_SOURCE + 1))
    with pytest.raises(ValueError):
        read_source(source)


def test_snapshot_tamper_scope_conflicts_and_invalid_utf8():
    with pytest.raises(ValueError):
        preview(Source(b"x", "wrong"), adapter="skill", scope="user", name="x")
    with pytest.raises(ValueError):
        preview(Source(b"x", digest(b"x")), adapter="skill", scope="recursive", name="x")
    with pytest.raises(ValueError):
        preview(Source(b"\xff", digest(b"\xff")), adapter="skill", scope="user", name="x")
    a = plan("same", adapter="instruction")
    data = b"same"
    b = preview(Source(data, digest(data)), adapter="instruction", scope="user", name="fixture")
    assert a["items"][0]["key"] != b["items"][0]["key"]
