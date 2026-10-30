"""Named delegation routes: the pure ``delegation.routes`` parser (D02, configuration only).

No router, no provider, no credentials: the parser must give the same answer everywhere. That a
resolved route reaches a child loop is a later slice and is not tested here.
"""

from __future__ import annotations

import pytest

from exo.core.agents.route_config import RESERVED_NAMES, RouteConfig, RouteConfigError


def test_missing_block_is_the_default_backend():
    for cfg in (None, {}, {"backend": "default"}):
        rc = RouteConfig.of(cfg)
        assert rc.backend == "default" and dict(rc.routes) == {} and rc.names() == ()
        with pytest.raises(RouteConfigError, match="no named routes"):
            rc.resolve("cheap")


def test_model_map_parses_and_resolves():
    rc = RouteConfig.of({"backend": "model_map", "routes": {"cheap": "fake:fast", "wide": "fake:deep-1"}})
    assert rc.backend == "model_map"
    assert rc.names() == ("cheap", "wide")
    assert rc.resolve("cheap") == "fake:fast"
    assert rc.resolve("wide") == "fake:deep-1"
    with pytest.raises(RouteConfigError, match="unknown route 'other'; configured: cheap, wide"):
        rc.resolve("other")


def test_routes_alone_imply_model_map_and_the_mapping_is_immutable():
    cfg = {"routes": {"cheap": "fake:fast"}}
    rc = RouteConfig.of(cfg)
    assert rc.backend == "model_map" and rc.resolve("cheap") == "fake:fast"
    cfg["routes"]["cheap"] = "fake:deep"  # the caller's dict changing later cannot move a child
    assert rc.resolve("cheap") == "fake:fast"
    with pytest.raises(TypeError):
        rc.routes["cheap"] = "fake:deep"  # type: ignore[index]


@pytest.mark.parametrize("cfg, why", [
    ({"backend": "default", "routes": {"cheap": "fake:fast"}}, "takes no routes"),
    ({"backend": "model_map"}, "non-empty routes map"),
    ({"backend": "model_map", "routes": {}}, "non-empty routes map"),
    ({"backend": "router", "routes": {"cheap": "fake:fast"}}, "backend must be one of"),
    ({"backend": "model_map", "routes": {"cheap": "fake:fast"}, "profile_routes": {}}, "unsupported key"),
    ({"backend": "model_map", "routes": {"cheap": "fake:fast"}, "credentials": {}}, "unsupported key"),
    ({"backend": "model_map", "routes": {"fast": "fake:fast"}}, "reserved"),
    ({"backend": "model_map", "routes": {"main": "fake:fast"}}, "reserved"),
    ({"backend": "model_map", "routes": {"aux": "fake:fast"}}, "reserved"),
    ({"backend": "model_map", "routes": {"delegate": "fake:fast"}}, "reserved"),
    ({"backend": "model_map", "routes": {"bad name": "fake:fast"}}, "bad route name"),
    ({"backend": "model_map", "routes": {"1cheap": "fake:fast"}}, "bad route name"),
    ({"backend": "model_map", "routes": {"x" * 33: "fake:fast"}}, "bad route name"),
    ({"backend": "model_map", "routes": {"cheap": "fast"}}, "provider:model"),
    ({"backend": "model_map", "routes": {"cheap": ":fast"}}, "provider:model"),
    ({"backend": "model_map", "routes": {"cheap": "fake:"}}, "provider:model"),
    ({"backend": "model_map", "routes": {"cheap": ""}}, "provider:model"),
    ({"backend": "model_map", "routes": {"cheap": 7}}, "provider:model"),
    ({"backend": "model_map", "routes": ["cheap"]}, "non-empty routes map"),
])
def test_bad_blocks_are_rejected_with_a_reason(cfg, why):
    with pytest.raises(RouteConfigError, match=why):
        RouteConfig.of(cfg)


def test_reserved_names_cover_router_roles_and_tiers():
    assert {"main", "aux", "delegate", "decision", "eval", "fast", "standard", "deep"} == set(RESERVED_NAMES)


def test_config_is_frozen_and_holds_no_router_or_credentials():
    rc = RouteConfig.of({"backend": "model_map", "routes": {"cheap": "fake:fast"}})
    with pytest.raises(Exception):
        rc.backend = "default"  # type: ignore[misc]
    assert {f for f in rc.__dataclass_fields__} == {"backend", "routes"}
