# Copyright 2013-2024 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

"""Schema for mirrors.yaml configuration file.

.. literalinclude:: _spack_root/lib/spack/spack/schema/mirrors.py
   :lines: 13-
"""
from typing import Any, Dict


string_or_variable = {
    "oneOf": [
        {
            "type": "object",
            "required": ["variable"],
            "additionalProperties": False,
            "properties": {"variable": {"type": "string"}},
        },
        {"type": "string"},
    ]
}

deprecate_access_token = {
    "deprecatedProperties": [
        {
            "names": ["access_token"],
            "message": "Spack no longer supportes plain text access_token in mirror configs",
            "error": False,
        }
    ]
}


#: Common properties for connection specification
connection = {
    "url": {"type": "string"},
    # todo: replace this with named keys "username" / "password" or "id" / "secret"
    "access_pair": {
        "oneOf": [
            {"type": "array", "items": {"minItems": 2, "maxItems": 2, "type": string_or_variable}},
            {
                "type": "object",
                "required": ["secret_variable"],
                # Only allow id or id_variable to be set, not both
                "oneOf": [{"required": ["id"]}, {"required": ["id_variable"]}],
                "properties": {
                    "id": {"type": "string"},
                    "id_variable": {"type": "string"},
                    "secret_variable": {"type": "string"},
                },
            },
        ]
    },
    "access_token": {"type": ["string", "null"]},  # deprecated
    "access_token_variable": {"type": ["string", "null"]},
    "profile": {"type": ["string", "null"]},
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
            **deprecate_access_token,
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
    **deprecate_access_token,
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
