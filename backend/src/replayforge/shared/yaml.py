"""Safe YAML with unambiguous mapping semantics for reviewed configuration."""

from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently replacing reviewed values."""


def _mapping(
    loader: UniqueKeySafeLoader, node: MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                None, None, "unhashable mapping key", key_node.start_mark
            ) from error
        if duplicate:
            raise ConstructorError(None, None, "duplicate mapping key", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)  # type: ignore[no-untyped-call]
    return mapping


UniqueKeySafeLoader.add_constructor(BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_unique_yaml(content: str) -> Any:
    return yaml.load(content, Loader=UniqueKeySafeLoader)
