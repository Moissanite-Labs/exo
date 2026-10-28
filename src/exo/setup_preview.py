"""Offline setup-import planning prototype (#502). No apply, discovery or authority translation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_SOURCE = 65_536
SECRET = re.compile(r"(?i)(?:sk-[a-z0-9_-]{8,}|(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s}$]+)")
NAME = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
CREDENTIAL_FLAG = re.compile(r"(?i)(?:^|[-_])(?:token|password|secret|api[-_]?key)(?:$|=)")
REFERENCE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}\Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Source:
    """Explicitly selected source; path is host-owned input, never an agent-discovered installation."""
    data: bytes
    sha256: str


def read_source(path: Path) -> Source:
    """Bounded regular-file read with no symlink at any path component; never follows skill links."""
    path = Path(os.path.abspath(path))
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except OSError as exc:
        raise ValueError("source must be a regular file without symlink components") from exc
    finally:
        os.close(directory)
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("regular source file required")
        data = source.read(MAX_SOURCE + 1)
    if len(data) > MAX_SOURCE:
        raise ValueError("source exceeds preview bound")
    return Source(data, digest(data))


def preview(source: Source, *, adapter: str, scope: str, name: str,
            current: dict[str, str] | None = None, imported: dict[str, str] | None = None) -> dict[str, Any]:
    """Map an explicit fixture-format subset into inert candidates, not Exo settings.

    current/imported hold host-computed payload hashes. Neither is read from the foreign installation.
    Instructions retain explicit user/project scope and are reference-only, never standing authority.
    """
    if (not isinstance(source, Source) or len(source.data) > MAX_SOURCE
            or source.sha256 != digest(source.data)):
        raise ValueError("invalid source snapshot")
    if scope not in ("user", "project") or not NAME.fullmatch(name):
        raise ValueError("explicit valid scope and name required")
    current, imported = current or {}, imported or {}
    candidates: list[dict[str, Any]] = []
    unsupported = []

    def add(kind: str, item_name: str, payload: Any) -> None:
        if not NAME.fullmatch(item_name):
            unsupported.append("invalid item name")
            return
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if SECRET.search(encoded):
            unsupported.append("credential-bearing item excluded")
            return
        key = f"{scope}/{kind}/{item_name}"
        incoming = digest(encoded.encode())
        local, prior = current.get(key), imported.get(key)
        if local == incoming:
            state = "unchanged"
        elif local is None:
            state = "local_deleted_conflict" if prior else "add"
        elif prior == local:
            state = "update"
        else:
            state = "conflict"
        candidates.append({"key": key, "kind": kind, "scope": scope, "payload": payload,
                           "payload_sha256": incoming, "target_sha256": local,
                           "previous_import_sha256": prior, "state": state,
                           "authority": "reference_only"})

    try:
        text = source.data.decode("utf-8")
        if adapter in ("instruction", "skill"):
            if not text.strip():
                raise ValueError("empty instruction or skill")
            add(adapter, name, {"text": text})
        elif adapter in ("claude_mcp_json", "codex_toml"):
            doc = json.loads(text) if adapter == "claude_mcp_json" else tomllib.loads(text)
            if not isinstance(doc, dict):
                raise ValueError("configuration must be a mapping")
            server_key = "mcpServers" if adapter == "claude_mcp_json" else "mcp_servers"
            servers = doc.get(server_key, {})
            if not isinstance(servers, dict) or len(servers) > 32:
                raise ValueError("bounded MCP mapping required")
            if set(doc) - {server_key}:
                unsupported.append("other settings excluded, including foreign approval rules")
            for server_name, server in servers.items():
                if (not isinstance(server_name, str) or not isinstance(server, dict)
                        or set(server) - {"command", "args", "env"}):
                    unsupported.append("unsupported MCP definition")
                    continue
                command, args, env = server.get("command"), server.get("args", []), server.get("env", {})
                if (not isinstance(command, str) or not command or len(command) > 1000
                        or not isinstance(args, list) or len(args) > 64
                        or any(not isinstance(a, str) or len(a) > 2000 or CREDENTIAL_FLAG.search(a) for a in args)
                        or not isinstance(env, dict) or len(env) > 32
                        or any(not isinstance(k, str) or not NAME.fullmatch(k)
                               or not isinstance(v, str) or not REFERENCE.fullmatch(v) for k, v in env.items())):
                    unsupported.append("invalid or credential-bearing MCP definition excluded")
                    continue
                add("mcp_stdio", server_name, {"command": command, "args": args, "env": env})
        else:
            raise ValueError("unsupported adapter")
    except (UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, RecursionError) as exc:
        raise ValueError("malformed source") from exc
    return {"schema": "exo.setup-preview.v1", "source_sha256": source.sha256, "adapter": adapter,
            "scope": scope, "items": candidates, "unsupported": unsupported, "apply_supported": False,
            "authority_translated": False}
