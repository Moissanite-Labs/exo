"""The existing policy YAML subset, extracted without changing parsing rules."""
from __future__ import annotations

import json
import re
from typing import Any


class PolicyError(ValueError):
    pass


def _strip_comment(line: str) -> str:
    q, i = "", 0
    while i < len(line):
        ch = line[i]
        if q:
            if ch == "\\" and q == '"':
                i += 2
                continue
            if ch == q:
                q = ""
        elif ch in "'\"" and (i == 0 or line[i - 1] in " \t:[{,-"):
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def _scalar(s: str) -> Any:
    s = s.strip()
    if s == "":
        return None
    if s[0] in "[{":
        val, rest = _flow(s, 0)
        if s[rest:].strip():
            raise PolicyError(f"trailing text after flow value: {s!r}")
        return val
    if s[0] == '"':
        if not s.endswith('"') or len(s) < 2:
            raise PolicyError(f"unterminated string: {s!r}")
        return json.loads(s)
    if s[0] == "'":
        if not s.endswith("'") or len(s) < 2:
            raise PolicyError(f"unterminated string: {s!r}")
        return s[1:-1].replace("''", "'")
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", s):
        return float(s)
    return s


def _flow(s: str, i: int) -> tuple[Any, int]:
    """Parse a flow value starting at s[i]; return (value, index after it)."""
    while i < len(s) and s[i] in " \t":
        i += 1
    if s[i] == "[":
        items: list[Any] = []
        i += 1
        while True:
            while i < len(s) and s[i] in " \t,":
                i += 1
            if i >= len(s):
                raise PolicyError(f"unterminated flow list: {s!r}")
            if s[i] == "]":
                return items, i + 1
            v, i = _flow(s, i)
            items.append(v)
    if s[i] == "{":
        mapping: dict[str, Any] = {}
        i += 1
        while True:
            while i < len(s) and s[i] in " \t,":
                i += 1
            if i >= len(s):
                raise PolicyError(f"unterminated flow map: {s!r}")
            if s[i] == "}":
                return mapping, i + 1
            k, i = _flow_atom(s, i, stop=":")
            if i >= len(s) or s[i] != ":":
                raise PolicyError(f"flow map key without value: {s!r}")
            v, i = _flow(s, i + 1)
            mapping[str(k)] = v
    return _flow_atom(s, i, stop=",]}")


def _flow_atom(s: str, i: int, stop: str) -> tuple[Any, int]:
    while i < len(s) and s[i] in " \t":
        i += 1
    if i < len(s) and s[i] in "[{":
        return _flow(s, i)
    if i < len(s) and s[i] in "'\"":
        q, j = s[i], i + 1
        while j < len(s):
            if s[j] == "\\" and q == '"':
                j += 2
                continue
            if s[j] == q:
                if q == "'" and j + 1 < len(s) and s[j + 1] == "'":
                    j += 2
                    continue
                break
            j += 1
        return _scalar(s[i:j + 1]), j + 1
    j = i
    while j < len(s) and s[j] not in stop:
        j += 1
    return _scalar(s[i:j]), j


_KEY = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s'"#\-\[\]{}][^:#]*?|-[^\s][^:#]*?)\s*:(?:\s+|$)(.*)$""")


def parse_yaml(text: str) -> Any:
    """Parse the YAML subset described above. Raises PolicyError on malformed input."""
    raw = text.replace("\t", "    ").splitlines()
    lines: list[tuple[int, str, int]] = []      # (indent, content, raw index)
    for n, line in enumerate(raw):
        body = _strip_comment(line)
        if body.strip() in ("---", "...") and not body.startswith(" "):
            continue
        if body.strip():
            lines.append((len(body) - len(body.lstrip(" ")), body.strip(), n))
    if not lines:
        return None
    val, i = _block(lines, 0, lines[0][0], raw)
    if i != len(lines):
        raise PolicyError(f"unexpected indentation at line {lines[i][2] + 1}: {lines[i][1]!r}")
    return val


def _block_scalar(lines, i, parent_indent, raw, style) -> tuple[str, int]:
    # Collect raw lines (keeping blank ones) indented deeper than the parent.
    start = lines[i - 1][2] + 1
    end_idx = i
    while end_idx < len(lines) and lines[end_idx][0] > parent_indent:
        end_idx += 1
    stop = lines[end_idx][2] if end_idx < len(lines) else len(raw)
    chunk = raw[start:stop]
    while chunk and not chunk[-1].strip():
        chunk.pop()
    ind = min((len(x) - len(x.lstrip(" ")) for x in chunk if x.strip()), default=0)
    chunk = [x[ind:] for x in chunk]
    if style.startswith("|"):
        text = "\n".join(chunk)
    else:
        paras, cur = [], []
        for x in chunk:
            if x.strip():
                cur.append(x.strip())
            else:
                paras.append(" ".join(cur))
                cur = []
        paras.append(" ".join(cur))
        text = "\n".join(p for p in paras)
    if not style.endswith("-"):
        text += "\n"
    return text, end_idx


def _block(lines, i, indent, raw) -> tuple[Any, int]:
    if lines[i][1].startswith("- ") or lines[i][1] == "-":
        return _list(lines, i, indent, raw)
    return _map(lines, i, indent, raw)


def _value_after_key(lines, i, indent, rest, raw) -> tuple[Any, int]:
    """Value for a `key:` at `indent` whose inline part is `rest`; `i` is the next line."""
    if rest in (">", ">-", ">+", "|", "|-", "|+"):
        return _block_scalar(lines, i, indent, raw, rest)
    if rest:
        return _scalar(rest), i
    if i < len(lines) and lines[i][0] > indent:
        return _block(lines, i, lines[i][0], raw)
    if i < len(lines) and lines[i][0] == indent and (lines[i][1].startswith("- ") or lines[i][1] == "-"):
        return _list(lines, i, indent, raw)
    return None, i


def _map(lines, i, indent, raw) -> tuple[dict, int]:
    out: dict = {}
    while i < len(lines) and lines[i][0] == indent:
        content = lines[i][1]
        if content.startswith("- "):
            break
        m = _KEY.match(content)
        if not m:
            raise PolicyError(f"expected 'key: value' at line {lines[i][2] + 1}: {content!r}")
        key = m.group(1)
        if key[0] in "'\"":
            key = _scalar(key)
        val, i = _value_after_key(lines, i + 1, indent, m.group(2).strip(), raw)
        out[str(key)] = val
    if i < len(lines) and lines[i][0] > indent:
        raise PolicyError(f"unexpected indentation at line {lines[i][2] + 1}: {lines[i][1]!r}")
    return out, i


def _list(lines, i, indent, raw) -> tuple[list, int]:
    out: list = []
    while i < len(lines) and lines[i][0] == indent and (lines[i][1].startswith("- ") or lines[i][1] == "-"):
        content = lines[i][1][1:].lstrip(" ")
        col = indent + (len(lines[i][1]) - len(content))
        if not content:
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                val, i = _block(lines, i + 1, lines[i + 1][0], raw)
            else:
                val, i = None, i + 1
            out.append(val)
            continue
        if _KEY.match(content) and content[0] not in "[{\"'":
            # mapping item: re-read this line as a mapping line at the content column
            lines[i] = (col, content, lines[i][2])
            val, i = _map(lines, i, col, raw)
            out.append(val)
            continue
        if content.startswith(("- ", "-")) and content != "-" and content[1:2] == " ":
            lines[i] = (col, content, lines[i][2])
            val, i = _list(lines, i, col, raw)
            out.append(val)
            continue
        out.append(_scalar(content))
        i += 1
    return out, i


