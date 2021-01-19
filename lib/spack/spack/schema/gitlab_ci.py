# Copyright 2013-2021 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

"""Schema for gitlab-ci.yaml configuration file.

.. literalinclude:: ../spack/schema/gitlab_ci.py
   :lines: 13-
"""

from llnl.util.lang import union_dicts

image_schema = {
    'oneOf': [
        {
            'type': 'string'
        }, {
            'type': 'object',
            'properties': {
                'name': {'type': 'string'},
                'entrypoint': {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                    },
                },
            },
        },
    ],
}

runner_attributes_schema_items = {
    'image': image_schema,
    'tags': {
        'type': 'array',
        'items': {'type': 'string'}
    },
    'variables': {
        'type': 'object',
        'patternProperties': {
            r'[\w\d\-_\.]+': {
                'type': 'string',
            },
        },
    },
}

customizable_job_schema_items = {
    'before_script': {
        'type': 'array',
        'items': {'type': 'string'}
    },
    'script': {
        'type': 'array',
        'items': {'type': 'string'}
    },
    'after_script': {
        'type': 'array',
        'items': {'type': 'string'}
    },
}

runner_selector_schema = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['tags'],
    'properties': runner_attributes_schema_items,
}

#: Properties for inclusion in other schemas
properties = {
    'gitlab-ci': {
        'type': 'object',
        'additionalProperties': False,
        'required': ['mappings'],
        'patternProperties': union_dicts(
            runner_attributes_schema_items,
            customizable_job_schema_items,
            {
                'bootstrap': {
                    'type': 'array',
                    'items': {
                        'anyOf': [
                            {
                                'type': 'string',
                            }, {
                                'type': 'object',
                                'additionalProperties': False,
                                'required': ['name'],
                                'properties': {
                                    'name': {
                                        'type': 'string',
                                    },
                                    'compiler-agnostic': {
                                        'type': 'boolean',
                                        'default': False,
                                    },
                                },
                            },
                        ],
                    },
                },
                'mappings': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'additionalProperties': False,
                        'required': ['match'],
                        'properties': {
                            'match': {
                                'type': 'array',
                                'items': {
                                    'type': 'string',
                                },
                            },
                            'runner-attributes': {
                                'type': 'object',
                                'additionalProperties': True,
                                'required': ['tags'],
                                'properties': union_dicts(
                                    runner_attributes_schema_items,
                                    customizable_job_schema_items
                                ),
                            },
                        },
                    },
                },
                'enable-artifacts-buildcache': {
                    'type': 'boolean',
                    'default': False,
                },
                'rebuild-index': {
                    'type': 'boolean',
                    'default': False,
                },
                'nonbuild-job-attributes': runner_selector_schema,
            }
        ),
    },
}


#: Full schema with metadata
schema = {
    '$schema': 'http://json-schema.org/schema#',
    'title': 'Spack gitlab-ci configuration file schema',
    'type': 'object',
    'additionalProperties': False,
    'properties': properties,
}
