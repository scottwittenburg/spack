# Copyright 2013-2018 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import pytest

from spack.spec import Spec
from jsonschema import ValidationError
from spack.spec_set import CombinatorialSpecSet


pytestmark = pytest.mark.usefixtures('config')


basic_yaml_file = {
    'spec-set': {
        'include': ['gmake'],
        'matrix': [
            {'packages': {
                'gmake': {
                    'versions': ['4.0']
                }
            }},
            {'compilers': {
                'gcc': {
                    'versions': ['4.2.1', '6.3.0']
                }, 'clang': {
                    'versions': ['8.0', '3.8']
                }
            }},
        ]
    }
}


def test_spec_set_basic():
    spec_set = CombinatorialSpecSet(basic_yaml_file, False)
    specs = list(spec for spec in spec_set)
    assert len(specs) == 4


def test_spec_set_no_include():
    yaml_file = {
        'spec-set': {
            'matrix': [
                {'packages': {
                    'gmake': {
                        'versions': ['4.0']
                    }
                }},
                {'compilers': {
                    'gcc': {
                        'versions': ['4.2.1', '6.3.0']
                    }, 'clang': {
                        'versions': ['8.0', '3.8']
                    }
                }},
            ]
        }
    }
    spec_set = CombinatorialSpecSet(yaml_file, False)
    specs = list(spec for spec in spec_set)
    assert len(specs) == 4


def test_spec_set_exclude():
    yaml_file = {
        'spec-set': {
            'include': ['gmake'],
            'exclude': ['gmake'],
            'matrix': [
                {'packages': {
                    'gmake': {
                        'versions': ['4.0']
                    }
                }},
                {'compilers': {
                    'gcc': {
                        'versions': ['4.2.1', '6.3.0']
                    }, 'clang': {
                        'versions': ['8.0', '3.8']
                    }
                }},
            ]
        }
    }
    spec_set = CombinatorialSpecSet(yaml_file, False)
    specs = list(spec for spec in spec_set)
    assert len(specs) == 0


def test_spec_set_include_limited_packages():
    yaml_file = {
        'spec-set': {
            'include': ['gmake'],
            'matrix': [
                {'packages': {
                    'gmake': {
                        'versions': ['4.0']
                    },
                    'appres': {
                        'versions': ['1.0.4']
                    },
                    'allinea-reports': {
                        'versions': ['6.0.4']
                    }
                }},
                {'compilers': {
                    'gcc': {
                        'versions': ['4.2.1', '6.3.0']
                    }, 'clang': {
                        'versions': ['8.0', '3.8']
                    }
                }},
            ]
        }
    }
    spec_set = CombinatorialSpecSet(yaml_file, False)
    specs = list(spec for spec in spec_set)
    assert len(specs) == 4


def test_spec_set_simple_spec_list():
    yaml_file = {
        'spec-set': {
            'matrix': [
                {'specs': [
                    'gmake@4.0',
                    'appres@1.0.4',
                    'allinea-reports@6.0.4'
                ]},
            ]
        }
    }
    spec_set = CombinatorialSpecSet(yaml_file, False)
    specs = list(spec for spec in spec_set)
    assert len(specs) == 3


def test_spec_set_with_specs():
    yaml_file = {
        'spec-set': {
            'include': ['gmake', 'appres'],
            'matrix': [
                {'specs': [
                    'gmake@4.0',
                    'appres@1.0.4',
                    'allinea-reports@6.0.4'
                ]},
                {'compilers': {
                    'gcc': {
                        'versions': ['4.2.1', '6.3.0']
                    }, 'clang': {
                        'versions': ['8.0', '3.8']
                    }
                }},
            ]
        }
    }
    spec_set = CombinatorialSpecSet(yaml_file, False)
    specs = list(spec for spec in spec_set)
    assert len(specs) == 8


def test_spec_set_compilers_bad_property():
    yaml_file = {
        'spec-set': {
            'foobar': ['gmake'],
            'matrix': [
                {'packages': {
                    'gmake': {'versions': ['4.0']},
                }},
                {'compilers': {
                    'gcc': {'versions': ['4.2.1', '6.3.0']},
                    'clang': {'versions': ['8.0', '3.8']},
                }},
            ]
        }
    }
    with pytest.raises(ValidationError):
        CombinatorialSpecSet(yaml_file)


def test_spec_set_packages_no_matrix():
    yaml_file = {
        'spec-set': {
            'include': ['gmake'],
            'packages': {
                'gmake': {
                    'versions': ['4.0']
                },
                'appres': {
                    'versions': ['1.0.4']
                },
                'allinea-reports': {
                    'versions': ['6.0.4']
                }
            },
        }
    }
    with pytest.raises(ValidationError):
        CombinatorialSpecSet(yaml_file)


def test_spec_set_get_cdash_string():
    yaml_file = {
        'spec-set': {
            'cdash': 'http://example.com/cdash',
            'project': 'testproj',
            'matrix': [
                {'packages': {
                    'gmake': {'versions': ['4.0']},
                }},
                {'compilers': {
                    'gcc': {'versions': ['4.2.1', '6.3.0']},
                    'clang': {'versions': ['8.0', '3.8']},
                }},
            ]
        }
    }

    spec_set = CombinatorialSpecSet(yaml_file)
    assert spec_set.cdash == ['http://example.com/cdash']
    assert spec_set.project == 'testproj'


def test_spec_set_get_cdash_array():
    yaml_file = {
        'spec-set': {
            'cdash': ['http://example.com/cdash', 'http://example.com/cdash2'],
            'project': 'testproj',
            'matrix': [
                {'packages': {
                    'gmake': {'versions': ['4.0']},
                }},
                {'compilers': {
                    'gcc': {'versions': ['4.2.1', '6.3.0']},
                    'clang': {'versions': ['8.0', '3.8']},
                }},
            ]
        }
    }

    spec_set = CombinatorialSpecSet(yaml_file)
    assert spec_set.cdash == [
        'http://example.com/cdash', 'http://example.com/cdash2']
    assert spec_set.project == 'testproj'


def test_compiler_specs():
    spec_set = CombinatorialSpecSet(basic_yaml_file, False)
    compilers = spec_set._compiler_specs({
        'gcc': {
            'versions': ['4.2.1', '6.3.0']
        }, 'clang': {
            'versions': ['8.0', '3.8']
        }})

    assert Spec('%gcc@4.2.1') in compilers
    assert Spec('%gcc@6.3.0') in compilers
    assert Spec('%clang@8.0') in compilers
    assert Spec('%clang@3.8') in compilers


def test_package_specs():
    spec_set = CombinatorialSpecSet(basic_yaml_file, False)

    packages = spec_set._package_specs({
        'gmake': {
            'versions': ['4.0', '5.0']
        },
        'appres': {
            'versions': ['1.0.4']
        },
        'allinea-reports': {
            'versions': ['6.0.1', '6.0.3', '6.0.4']
        }
    })

    assert Spec('gmake@4.0') in packages
    assert Spec('gmake@5.0') in packages
    assert Spec('appres@1.0.4') in packages
    assert Spec('allinea-reports@6.0.1') in packages
    assert Spec('allinea-reports@6.0.3') in packages
    assert Spec('allinea-reports@6.0.4') in packages
