"""exo.bus: signed, hash-chained messages between agent seats (port of Divan Bus v0.1).

- `wire`: envelope, chain hash, sealed bodies, signed read headers (wire compatible with Divan Bus).
- `client.Client`: post, inbox, thread, verify, tail. Stdlib plus PyNaCl.
- `registry`, `store`: the signed seat registry and the append-only SQLite log.
- `relay.create_app`: the FastAPI relay (needs the `bus` extra). `mcp_server`: optional MCP tools.
- `tools`: registry builder and checkpoint writer.

Configuration comes from `EXO_BUS_*` (or legacy `DIVAN_*`) variables; see `config`.
"""
from .client import BusError, Client, keygen, render_body
from .config import ClientConfig, RelayConfig

__all__ = ["BusError", "Client", "ClientConfig", "RelayConfig", "keygen", "render_body"]
