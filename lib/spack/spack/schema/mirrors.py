# Copyright 2013-2024 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

"""Schema for mirrors.yaml configuration file.

.. literalinclude:: _spack_root/lib/spack/spack/schema/mirrors.py
   :lines: 13-
"""
from functools import lru_cache
from typing import Any, Dict

from llnl.util.lang import union_dicts


@lru_cache(maxsize=2)
def credential_schema(allow_plain_text: bool = False):
    """Get the schema for a mirror crednetial field"""
    return {
        "oneOf": [
            {
                "type": "object",
                "required": "variable",
                "additionalProperties": False,
                "properties": {"variable": {"type": "string"}},
            },
            {"type": "string"},
        ]
    }


#: Common properties for connection specification
connection = {
    "url": {"type": "string"},
    # todo: replace this with named keys "username" / "password" or "id" / "secret"
    "access_pair": {
        "type": "array",
        "prefixItems": [
            # The first item in the list should always be allowed as plain text (username)
            credential_schema(allow_plain_text=True),
            credential_schema(),
        ],
        "items": {"minItems": 2, "maxItems": 2, "type": credential_schema()},
    },
    "access_token": credential_schema(),
    "profile": credential_schema(allow_plain_text=True),
    "endpoint_url": {"type": ["string", "null"]},
}

#: Mirror connection inside pull/push keys
fetch_and_push = {
    "anyOf": [
        {"type": "string"},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {**connection},  # type: ignore
        },
    ]
}

#: Mirror connection when no pull/push keys are set
mirror_entry = {
    "type": "object",
    "additionalProperties": False,
    "anyOf": [{"required": ["url"]}, {"required": ["fetch"]}, {"required": ["pull"]}],
    "properties": {
        "source": {"type": "boolean"},
        "binary": {"type": "boolean"},
        "signed": {"type": "boolean"},
        "fetch": fetch_and_push,
        "push": fetch_and_push,
        "autopush": {"type": "boolean"},
        **connection,  # type: ignore
    },
}

#: Properties for inclusion in other schemas
properties: Dict[str, Any] = {
    "mirrors": {
        "type": "object",
        "default": {},
        "additionalProperties": False,
        "patternProperties": {r"\w[\w-]*": {"anyOf": [{"type": "string"}, mirror_entry]}},
    }
}


#: Full schema with metadata
schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Spack mirror configuration file schema",
    "type": "object",
    "additionalProperties": False,
    "properties": properties,
}
