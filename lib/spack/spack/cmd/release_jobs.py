# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import base64
import json
import os
import zlib

from six import iteritems
from six.moves.urllib.error import HTTPError, URLError
from six.moves.urllib.parse import urlencode
from six.moves.urllib.request import build_opener, HTTPHandler, Request

import llnl.util.tty as tty

import spack.environment as ev
import spack.compilers as compilers
from spack.dependency import all_deptypes
from spack.error import SpackError
import spack.hash_types as ht
from spack.spec import Spec
import spack.util.spack_yaml as syaml

description = "generate release build set as .gitlab-ci.yml"
section = "build"
level = "long"


def setup_parser(subparser):
    subparser.add_argument(
        '-o', '--output-file', default=".gitlab-ci.yml",
        help="path to output file to write")

    subparser.add_argument(
        '-p', '--print-summary', action='store_true', default=False,
        help="Print summary of staged jobs to standard output")

    subparser.add_argument(
        '--cdash-credentials', default=None,
        help="Path to file containing CDash authentication token")


def _create_buildgroup(opener, headers, url, project, group_name, group_type):
    data = {
        "newbuildgroup": group_name,
        "project": project,
        "type": group_type
    }

    enc_data = json.dumps(data).encode('utf-8')

    request = Request(url, data=enc_data, headers=headers)

    response = opener.open(request)
    response_code = response.getcode()

    if response_code != 200 and response_code != 201:
        msg = 'Creating buildgroup failed (response code = {0}'.format(
            response_code)
        raise SpackError(msg)

    response_text = response.read()
    response_json = json.loads(response_text)
    build_group_id = response_json['id']

    return build_group_id


def populate_buildgroup(job_names, group_name, project, site,
                        credentials, cdash_url):
    url = "{0}/api/v1/buildgroup.php".format(cdash_url)

    headers = {
        'Authorization': 'Bearer {0}'.format(credentials),
        'Content-Type': 'application/json',
    }

    opener = build_opener(HTTPHandler)

    parent_group_id = _create_buildgroup(
        opener, headers, url, project, group_name, 'Daily')
    group_id = _create_buildgroup(
        opener, headers, url, project, 'Latest {0}'.format(group_name),
        'Latest')

    if not parent_group_id or not group_id:
        msg = 'Failed to create or retrieve buildgroups for {0}'.format(
            group_name)
        raise SpackError(msg)

    data = {
        'project': project,
        'buildgroupid': group_id,
        'dynamiclist': [{
            'match': name,
            'parentgroupid': parent_group_id,
            'site': site
        } for name in job_names]
    }

    enc_data = json.dumps(data).encode('utf-8')

    request = Request(url, data=enc_data, headers=headers)
    request.get_method = lambda: 'PUT'

    response = opener.open(request)
    response_code = response.getcode()

    if response_code != 200:
        msg = 'Error response code ({0}) in populate_buildgroup'.format(
            response_code)
        raise SpackError(msg)


def is_main_phase(phase_name):
    return True if phase_name == 'specs' else False


def get_job_name(phase, strip_compiler, spec, osarch, build_group):
    item_idx = 0
    format_str = ''
    format_args = []

    if phase:
        format_str += '({{{0}}})'.format(item_idx)
        format_args.append(phase)
        item_idx += 1

    format_str += ' {{{0}}}'.format(item_idx)
    format_args.append(spec.name)
    item_idx += 1

    format_str += ' {{{0}}}'.format(item_idx)
    format_args.append(spec.version)
    item_idx += 1

    if is_main_phase(phase) is True or strip_compiler is False:
        format_str += ' {{{0}}}'.format(item_idx)
        format_args.append(spec.compiler)
        item_idx += 1

    format_str += ' {{{0}}}'.format(item_idx)
    format_args.append(osarch)
    item_idx += 1

    if build_group:
        format_str += ' {{{0}}}'.format(item_idx)
        format_args.append(build_group)
        item_idx += 1

    return format_str.format(*format_args)


def get_cdash_build_name(spec, build_group):
    return '{0}@{1}%{2} arch={3} ({4})'.format(
        spec.name, spec.version, spec.compiler, spec.architecture, build_group)


def get_spec_string(spec):
    format_elements = [
        '{name}{@version}',
        '{%compiler}',
    ]

    if spec.architecture:
        format_elements.append(' {arch=architecture}')

    return spec.format(''.join(format_elements))


def format_root_spec(spec, main_phase, strip_compiler):
    if main_phase is False and strip_compiler is True:
        return '{0}@{1} arch={2}'.format(
            spec.name, spec.version, spec.architecture)
    else:
        spec_yaml = spec.to_yaml(hash=ht.build_hash).encode('utf-8')
        return str(base64.b64encode(zlib.compress(spec_yaml)).decode('utf-8'))
        # return '{0}@{1}%{2} arch={3}'.format(
        #     spec.name, spec.version, spec.compiler, spec.architecture)


def spec_deps_key_label(s):
    return s.dag_hash(), "%s/%s" % (s.name, s.dag_hash(7))


def _add_dependency(spec_label, dep_label, deps):
    if spec_label == dep_label:
        return
    if spec_label not in deps:
        deps[spec_label] = set()
    deps[spec_label].add(dep_label)


def get_spec_dependencies(specs, deps, spec_labels):
    spec_deps_obj = compute_spec_deps(specs)

    if spec_deps_obj:
        dependencies = spec_deps_obj['dependencies']
        specs = spec_deps_obj['specs']

        for entry in specs:
            spec_labels[entry['label']] = {
                'spec': Spec(entry['spec']),
                'rootSpec': entry['root_spec'],
            }

        for entry in dependencies:
            _add_dependency(entry['spec'], entry['depends'], deps)


def stage_spec_jobs(specs):
    """Take a set of release specs and generate a list of "stages", where the
        jobs in any stage are dependent only on jobs in previous stages.  This
        allows us to maximize build parallelism within the gitlab-ci framework.

    Arguments:
        specs (Iterable): Specs to build

    Returns: A tuple of information objects describing the specs, dependencies
        and stages:

        spec_labels: A dictionary mapping the spec labels which are made of
            (pkg-name/hash-prefix), to objects containing "rootSpec" and "spec"
            keys.  The root spec is the spec of which this spec is a dependency
            and the spec is the formatted spec string for this spec.

        deps: A dictionary where the keys should also have appeared as keys in
            the spec_labels dictionary, and the values are the set of
            dependencies for that spec.

        stages: An ordered list of sets, each of which contains all the jobs to
            built in that stage.  The jobs are expressed in the same format as
            the keys in the spec_labels and deps objects.

    """

    # The convenience method below, "remove_satisfied_deps()", does not modify
    # the "deps" parameter.  Instead, it returns a new dictionary where only
    # dependencies which have not yet been satisfied are included in the
    # return value.
    def remove_satisfied_deps(deps, satisfied_list):
        new_deps = {}

        for key, value in iteritems(deps):
            new_value = set([v for v in value if v not in satisfied_list])
            if new_value:
                new_deps[key] = new_value

        return new_deps

    deps = {}
    spec_labels = {}

    get_spec_dependencies(specs, deps, spec_labels)

    # Save the original deps, as we need to return them at the end of the
    # function.  In the while loop below, the "dependencies" variable is
    # overwritten rather than being modified each time through the loop,
    # thus preserving the original value of "deps" saved here.
    dependencies = deps
    unstaged = set(spec_labels.keys())
    stages = []

    while dependencies:
        dependents = set(dependencies.keys())
        next_stage = unstaged.difference(dependents)
        stages.append(next_stage)
        unstaged.difference_update(next_stage)
        # Note that "dependencies" is a dictionary mapping each dependent
        # package to the set of not-yet-handled dependencies.  The final step
        # below removes all the dependencies that are handled by this stage.
        dependencies = remove_satisfied_deps(dependencies, next_stage)

    if unstaged:
        stages.append(unstaged.copy())

    return spec_labels, deps, stages


def print_staging_summary(spec_labels, dependencies, stages):
    if not stages:
        return

    tty.msg('  Staging summary:')
    stage_index = 0
    for stage in stages:
        tty.msg('    stage {0} ({1} jobs):'.format(stage_index, len(stage)))

        for job in sorted(stage):
            s = spec_labels[job]['spec']
            tty.msg('      {0} -> {1}'.format(job, get_spec_string(s)))

        stage_index += 1


def compute_spec_deps(spec_list):
    """
    Computes all the dependencies for the spec(s) and generates a JSON
    object which provides both a list of unique spec names as well as a
    comprehensive list of all the edges in the dependency graph.  For
    example, given a single spec like 'readline@7.0', this function
    generates the following JSON object:

    .. code-block:: JSON

       {
           "dependencies": [
               {
                   "depends": "readline/ip6aiun",
                   "spec": "readline/ip6aiun"
               },
               {
                   "depends": "ncurses/y43rifz",
                   "spec": "readline/ip6aiun"
               },
               {
                   "depends": "ncurses/y43rifz",
                   "spec": "readline/ip6aiun"
               },
               {
                   "depends": "pkgconf/eg355zb",
                   "spec": "ncurses/y43rifz"
               },
               {
                   "depends": "pkgconf/eg355zb",
                   "spec": "readline/ip6aiun"
               }
           ],
           "specs": [
               {
                 "root_spec": "readline@7.0%clang@9.1.0-apple arch=darwin-...",
                 "spec": "readline@7.0%clang@9.1.0-apple arch=darwin-highs...",
                 "label": "readline/ip6aiun"
               },
               {
                 "root_spec": "readline@7.0%clang@9.1.0-apple arch=darwin-...",
                 "spec": "ncurses@6.1%clang@9.1.0-apple arch=darwin-highsi...",
                 "label": "ncurses/y43rifz"
               },
               {
                 "root_spec": "readline@7.0%clang@9.1.0-apple arch=darwin-...",
                 "spec": "pkgconf@1.5.4%clang@9.1.0-apple arch=darwin-high...",
                 "label": "pkgconf/eg355zb"
               }
           ]
       }

    """
    deptype = all_deptypes
    spec_labels = {}

    specs = []
    dependencies = []

    def append_dep(s, d):
        dependencies.append({
            'spec': s,
            'depends': d,
        })

    for spec in spec_list:
        spec.concretize()

        # root_spec = get_spec_string(spec)
        root_spec = spec

        rkey, rlabel = spec_deps_key_label(spec)

        for s in spec.traverse(deptype=deptype):
            skey, slabel = spec_deps_key_label(s)
            spec_labels[slabel] = {
                'spec': get_spec_string(s),
                'root': root_spec,
            }
            append_dep(rlabel, slabel)

            for d in s.dependencies(deptype=deptype):
                dkey, dlabel = spec_deps_key_label(d)
                append_dep(slabel, dlabel)

    for l, d in spec_labels.items():
        specs.append({
            'label': l,
            'spec': d['spec'],
            'root_spec': d['root'],
        })

    deps_json_obj = {
        'specs': specs,
        'dependencies': dependencies,
    }

    return deps_json_obj


def spec_matches(spec, match_string):
    return spec.satisfies(match_string)


def find_matching_config(spec, ci_mappings):
    for ci_mapping in ci_mappings:
        for match_string in ci_mapping['match']:
            if spec_matches(spec, match_string):
                return ci_mapping['runner-attributes']
    return None


def generate_jobs(output_file, print_summary=False, cdash_credentials=None):
    env = ev.Environment(os.getcwd())

