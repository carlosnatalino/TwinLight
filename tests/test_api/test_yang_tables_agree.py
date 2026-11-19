"""The two tables derived from the T-API v2.6.0 YANG describe one schema.

``api/yang_config.py`` says which data nodes are configuration, for the
``content`` query parameter. ``models/tapi_schema_tree.py`` holds every data
node with its ``config`` statement, for refusing request members the schema
does not define and for what a PUT may change. They were derived separately
from the same ``pyang`` trees. Where they disagreed, ``content=config`` would
return a node that a PUT ignores as state, or leave out one it replaces.
"""

from __future__ import annotations

from twinlight.api.restconf_content import SCHEMA
from twinlight.models import tapi_schema


def _disagreements(schema_node, config_node, path: str, found: list[str]) -> int:
    """Compare one level and every configuration subtree below it; the number
    of nodes compared. ``yang_config`` does not list what lies under a config
    false node, so the comparison stops there."""
    compared = 0
    seen = set()
    for member, child in schema_node.iter_children():
        qualified = f"{child.module}:{child.name}"
        seen.add(qualified)
        where = f"{path}/{member}"
        other = config_node.children.get(qualified)
        if other is None:
            found.append(f"{where}: in tapi_schema_tree, not in yang_config")
            continue
        compared += 1
        if child.config != other.config:
            found.append(
                f"{where}: config {child.config} in tapi_schema_tree, {other.config} in yang_config"
            )
        elif child.config and child.has_children != (other.kind in ("container", "list")):
            found.append(f"{where}: {child.kind} in tapi_schema_tree, {other.kind} in yang_config")
        elif child.config and child.kind == "list" and tuple(child.keys) != tuple(other.keys):
            found.append(f"{where}: keys {child.keys} in tapi_schema_tree, {other.keys} in yang_config")
        elif child.config and child.has_children:
            compared += _disagreements(child, other, where, found)
    found.extend(
        f"{path}/{name}: in yang_config, not in tapi_schema_tree"
        for name in sorted(set(config_node.children) - seen)
    )
    return compared


def test_the_configuration_table_and_the_schema_tree_agree() -> None:
    context = "tapi-common:context"
    found: list[str] = []
    compared = _disagreements(
        tapi_schema.node(f"/{context}"), SCHEMA.children[context], f"/{context}", found
    )
    assert found == []
    # The whole configuration tree was walked, not an empty one.
    assert compared > 1000
