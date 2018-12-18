# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os
import re

import subprocess
from jsonschema import validate
from six import iteritems

import llnl.util.tty as tty

from spack.architecture import sys_type
from spack.dependency import all_deptypes
from spack.spec import Spec
from spack.paths import spack_root
from spack.error import SpackError
from spack.schema.os_container_mapping import schema
from spack.util.spec_set import CombinatorialSpecSet
import spack.util.spack_yaml as syaml

description = "generate release build set as .gitlab-ci.yml"
section = "build"
level = "long"


CMD_ERROR_OUTPUT_REGEX = re.compile(
    r'==>\s+Error:\s+(.+)<BEGIN_SPACK_COMMAND_OUTPUT>',
    re.MULTILINE | re.DOTALL)
IGNORE_SURROUND_REGEX = re.compile(
    r'<BEGIN_SPACK_COMMAND_OUTPUT>(.+)<END_SPACK_COMMAND_OUTPUT>',
    re.MULTILINE | re.DOTALL)
DEP_LINE_REGEX = re.compile(r'^([^\s]+) -> (.+)$', re.MULTILINE)
SPEC_LINE_REGEX = re.compile(r'^label: ([^,]+), spec: (.+)$', re.MULTILINE)


def setup_parser(subparser):
    subparser.add_argument(
        '-s', '--spec-set', default=None,
        help="path to release spec-set yaml file")

    subparser.add_argument(
        '-m', '--mirror-url', default='http://172.17.0.1:8081/',
        help="url of binary mirror where builds should be pushed")

    subparser.add_argument(
        '-o', '--output-file', default=".gitlab-ci.yml",
        help="path to output file to write")

    subparser.add_argument(
        '-t', '--shared-runner-tag', default=None,
        help="tag to add to jobs for shared runner selection")

    subparser.add_argument(
        '-k', '--signing-key', default=None,
        help="hash of gpg key to use for package signing")

    subparser.add_argument(
        '-c', '--cdash-url', default='https://cdash.spack.io',
        help="Base url of CDash instance jobs should communicate with")

    subparser.add_argument(
        '-p', '--print-summary', action='store_true', default=False,
        help="Print summary of staged jobs to standard output")

    subparser.add_argument(
        '--spec-deps', default=None,
        help="The spec for which you want container-generated deps")

    subparser.add_argument(
        '--this-machine-only', action='store_true', default=False,
        help="Use only the current machine to concretize specs, " +
        "instead of iterating over items in os-container-mapping.yaml " +
        "and using docker run")


def get_job_name(spec, osarch):
    return '{0} {1} {2} {3}'.format(spec.name, spec.version,
                                    spec.compiler, osarch)


def get_spec_string(spec):
    if spec.architecture:
        return '{0}@{1}%{2} arch={3}'.format(spec.name, spec.version,
                                             spec.compiler, spec.architecture)
    return '{0}@{1}%{2}'.format(spec.name, spec.version, spec.compiler)


def get_deps_using_container(spec, deps, spec_labels, image):
    image_home_dir = '/home/spackuser'
    repo_mount_location = '{0}/spack'.format(image_home_dir)
    entry_point = '{0}/spackcommand/spack-container-command.sh'.format(
        image_home_dir)

    cmd_to_run = [
        'docker', 'run', '--rm',
        '-v', '{0}:{1}'.format(spack_root, repo_mount_location),
        '--entrypoint', entry_point,
        '-t', str(image),
        '{0}/bin'.format(repo_mount_location),
        'spack', 'release-jobs', '--spec-deps', str(spec),
    ]

    def add_dep(s, d):
        if s == d:
            return
        if s not in deps:
            deps[s] = set()
        deps[s].add(d)

    tty.msg('Running subprocess command:')
    print(' '.join(cmd_to_run))
    proc = subprocess.Popen(cmd_to_run,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    proc.wait()

    out = proc.stdout.read()
    if out:
        m = CMD_ERROR_OUTPUT_REGEX.search(out)
        if m:
            tty.error('Encountered spack error running command in container:')
            print(m.group(1))

        m1 = IGNORE_SURROUND_REGEX.search(out)
        specs_and_deps = m1.group(1).strip()
        if specs_and_deps:
            spec_label_tuples = SPEC_LINE_REGEX.findall(specs_and_deps)

            for label, spec_str in spec_label_tuples:
                spec_labels[label.strip()] = {
                    'spec': Spec(spec_str.strip()),
                    'rootSpec': spec,
                }

            dep_tuples = DEP_LINE_REGEX.findall(specs_and_deps)

            for s, d in dep_tuples:
                add_dep(s.strip(), d.strip())


def get_spec_dependencies(spec, deps, spec_labels, image=None):
    if image:
        get_deps_using_container(spec, deps, spec_labels, image)
    else:
        compute_spec_deps(spec, deps, spec_labels)


def stage_spec_jobs(spec_set, containers, current_system=None):
    def remove_satisfied_deps(deps, satisfied_list):
        new_deps = {}

        for key, value in iteritems(deps):
            new_value = set([v for v in value if v not in satisfied_list])
            if new_value:
                new_deps[key] = new_value

        return new_deps

    deps = {}
    spec_labels = {}

    if current_system:
        os_names = [current_system]
    else:
        os_names = [name for name in containers]

    for spec in spec_set:
        for osname in os_names:
            container_info = containers[osname]
            image = None if current_system else container_info['image']
            if 'compilers' in container_info:
                found_one = False
                for item in container_info['compilers']:
                    if spec.compiler.satisfies(item['name']):
                        get_spec_dependencies(
                            spec, deps, spec_labels, image)
                        found_one = True
                if not found_one:
                    print('no compiler in {0} satisfied {1}'.format(
                        osname, spec.compiler))

    dependencies = deps
    unstaged = set(spec_labels.keys())
    stages = []

    while deps:
        depends_on = set(deps.keys())
        next_stage = unstaged.difference(depends_on)
        stages.append(next_stage)
        unstaged.difference_update(next_stage)
        deps = remove_satisfied_deps(deps, next_stage)

    if unstaged:
        stages.append(unstaged.copy())

    return spec_labels, dependencies, stages


def print_staging_summary(spec_labels, dependencies, stages):
    if not stages:
        return

    tty.msg('Staging summary:')
    stage_index = 0
    for stage in stages:
        tty.msg('  stage {0} ({1} jobs):'.format(stage_index, len(stage)))

        for job in sorted(stage):
            s = spec_labels[job]['spec']
            tty.msg('    {0} -> {1}'.format(job, get_spec_string(s)))

        stage_index += 1


def compute_spec_deps(spec, deps, spec_labels, write_to_stdout=False):
    deptype = all_deptypes
    spec_labels_internal = {}

    def key_label(s):
        return s.dag_hash(), "%s/%s" % (s.name, s.dag_hash(7))

    def add_dep(s, d):
        if s == d:
            return
        if s not in deps:
            deps[s] = set()
        deps[s].add(d)

    spec.concretize()

    rkey, rlabel = key_label(spec)

    for s in spec.traverse(deptype=deptype):
        if not s.concrete:
            s.concretize()
        skey, slabel = key_label(s)
        spec_labels_internal[slabel] = s
        add_dep(rlabel, slabel)

        for d in s.dependencies(deptype=deptype):
            dkey, dlabel = key_label(d)
            add_dep(slabel, dlabel)

    for label in spec_labels_internal:
        s = spec_labels_internal[label]
        spec_labels[label] = {
            'spec': s,
            'rootSpec': spec,
        }
        if write_to_stdout:
            print('label: {0}, spec: {1}'.format(label, get_spec_string(s)))

    if write_to_stdout:
        for dep_key in deps:
            for depends in deps[dep_key]:
                print('{0} -> {1}'.format(dep_key, depends))


def release_jobs(parser, args):
    share_path = os.path.join(spack_root, 'share', 'spack', 'docker')
    os_container_mapping_path = os.path.join(
        share_path, 'os-container-mapping.yaml')

    with open(os_container_mapping_path, 'r') as fin:
        os_container_mapping = syaml.load(fin)

    validate(os_container_mapping, schema)

    containers = os_container_mapping['containers']

    if args.spec_deps:
        # Just print out the spec labels and all dependency edges
        s = Spec(args.spec_deps)
        compute_spec_deps(s, {}, {}, True)
        return

    this_machine_only = args.this_machine_only
    current_system = sys_type() if this_machine_only else None

    release_specs_path = args.spec_set
    if not release_specs_path:
        raise SpackError('Must provide path to release spec-set')

    release_spec_set = CombinatorialSpecSet.from_file(release_specs_path)

    mirror_url = args.mirror_url

    if not mirror_url:
        raise SpackError('Must provide url of target binary mirror')

    cdash_url = args.cdash_url

    spec_labels, dependencies, stages = stage_spec_jobs(
        release_spec_set, containers, current_system)

    if not stages:
        tty.msg('No jobs staged, exiting.')
        return

    if args.print_summary:
        print_staging_summary(spec_labels, dependencies, stages)

    output_object = {}
    job_count = 0

    stage_names = ['stage-{0}'.format(i) for i in range(len(stages))]
    stage = 0

    for stage_jobs in stages:
        stage_name = stage_names[stage]

        for spec_label in stage_jobs:
            release_spec = spec_labels[spec_label]['spec']
            root_spec = spec_labels[spec_label]['rootSpec']

            pkg_compiler = release_spec.compiler
            pkg_hash = release_spec.dag_hash()

            osname = str(release_spec.architecture)
            job_name = get_job_name(release_spec, osname)
            container_info = containers[osname]
            build_image = container_info['image']

            job_scripts = ['./rebuild-package.sh']

            if 'setup_script' in container_info:
                job_scripts.insert(
                    0, container_info['setup_script'] % pkg_compiler)

            job_dependencies = []
            if spec_label in dependencies:
                job_dependencies = (
                    [get_job_name(spec_labels[dep_label]['spec'], osname)
                        for dep_label in dependencies[spec_label]])

            job_object = {
                'stage': stage_name,
                'variables': {
                    'MIRROR_URL': mirror_url,
                    'CDASH_BASE_URL': cdash_url,
                    'HASH': pkg_hash,
                    'DEPENDENCIES': ';'.join(job_dependencies),
                    'ROOT_SPEC': get_spec_string(root_spec),
                },
                'script': job_scripts,
                'image': build_image,
                'artifacts': {
                    'paths': [
                        'local_mirror/build_cache',
                        'jobs_scratch_dir',
                    ],
                    'when': 'always',
                },
                'dependencies': job_dependencies,
            }

            # If we see 'compilers' in the container iformation, it's a
            # filter for the compilers this container can handle, else we
            # assume it can handle any compiler
            if 'compilers' in container_info:
                do_job = False
                for item in container_info['compilers']:
                    if pkg_compiler.satisfies(item['name']):
                        do_job = True
            else:
                do_job = True

            if args.shared_runner_tag:
                job_object['tags'] = [args.shared_runner_tag]

            if args.signing_key:
                job_object['variables']['SIGN_KEY_HASH'] = args.signing_key

            if do_job:
                output_object[job_name] = job_object
                job_count += 1

        stage += 1

    tty.msg('{0} build jobs generated in {1} stages'.format(
        job_count, len(stages)))

    final_stage = 'stage-rebuild-index'

    final_job = {
        'stage': final_stage,
        'variables': {
            'MIRROR_URL': mirror_url,
        },
        'image': build_image,
        'script': './rebuild-index.sh',
    }

    if args.shared_runner_tag:
        final_job['tags'] = [args.shared_runner_tag]

    output_object['rebuild-index'] = final_job
    stage_names.append(final_stage)
    output_object['stages'] = stage_names

    with open(args.output_file, 'w') as outf:
        outf.write(syaml.dump(output_object))
