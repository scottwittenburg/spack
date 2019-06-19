# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import json

from jsonschema import validate, ValidationError
from six import iteritems
from six.moves.urllib.error import HTTPError, URLError
from six.moves.urllib.parse import urlencode
from six.moves.urllib.request import build_opener, HTTPHandler, Request

import llnl.util.tty as tty

import spack.environment as ev
from spack.dependency import all_deptypes
from spack.error import SpackError
from spack.spec import Spec
from spack.schema.specs_deps import schema as specs_deps_schema
import spack.util.spack_yaml as syaml

description = "generate release build set as .gitlab-ci.yml"
section = "build"
level = "long"


def setup_parser(subparser):
    subparser.add_argument(
        '-f', '--force', action='store_true', default=False,
        help="Force re-concretization of environment first")

    subparser.add_argument(
        '-o', '--output-file', default=".gitlab-ci.yml",
        help="path to output file to write")

    subparser.add_argument(
        '-k', '--signing-key', default=None,
        help="hash of gpg key to use for package signing")

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

    request = Request(url, data=json.dumps(data), headers=headers)

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

    request = Request(url, data=json.dumps(data), headers=headers)
    request.get_method = lambda: 'PUT'

    response = opener.open(request)
    response_code = response.getcode()

    if response_code != 200:
        msg = 'Error response code ({0}) in populate_buildgroup'.format(
            response_code)
        raise SpackError(msg)


def get_job_name(spec, osarch, build_group):
    return '{0} {1} {2} {3} {4}'.format(
        spec.name, spec.version, spec.compiler, osarch, build_group)


def get_cdash_build_name(spec, build_group):
    return '{0}@{1}%{2} arch={3} ({4})'.format(
        spec.name, spec.version, spec.compiler, spec.architecture, build_group)


def get_spec_string(spec, compiler_job=False):
    format_elements = [
        '{name}{@version}',
    ]

    if not compiler_job:
        # For compiler jobs, we don't know what compiler may be available to
        # build the compiler and it's dependencies, so we leave it out here.
        format_elements.append('{%compiler}')

    if spec.architecture:
        format_elements.append('{arch=architecture}')

    return spec.format(''.join(format_elements))


def spec_deps_key_label(s):
    return s.dag_hash(), "%s/%s" % (s.name, s.dag_hash(7))


def _add_dependency(spec_label, dep_label, deps):
    if spec_label == dep_label:
        return
    if spec_label not in deps:
        deps[spec_label] = set()
    deps[spec_label].add(dep_label)


def get_spec_dependencies(specs, deps, spec_labels, compiler_job):
    spec_deps_obj = compute_spec_deps(specs, compiler_job)

    try:
        validate(spec_deps_obj, specs_deps_schema)
    except ValidationError as val_err:
        tty.error('Ill-formed specs dependencies JSON object')
        tty.error(spec_deps_obj)
        tty.debug(val_err)
        return

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


def stage_spec_jobs(specs, compiler_job=False):
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

    get_spec_dependencies(specs, deps, spec_labels, compiler_job)

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

    tty.msg('Staging summary:')
    stage_index = 0
    for stage in stages:
        tty.msg('  stage {0} ({1} jobs):'.format(stage_index, len(stage)))

        for job in sorted(stage):
            s = spec_labels[job]['spec']
            tty.msg('    {0} -> {1}'.format(job, get_spec_string(s)))

        stage_index += 1


def compute_spec_deps(spec_list, compiler_job, stream_like=None):
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

    The object can be optionally written out to some stream.  This is
    useful, for example, when we need to concretize and generate the
    dependencies of a spec in a specific docker container.

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

        root_spec = get_spec_string(spec, compiler_job)

        rkey, rlabel = spec_deps_key_label(spec)

        for s in spec.traverse(deptype=deptype):
            skey, slabel = spec_deps_key_label(s)
            spec_labels[slabel] = {
                'spec': get_spec_string(s, compiler_job),
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

    if stream_like:
        stream_like.write(json.dumps(deps_json_obj))

    return deps_json_obj


def spec_matches(spec, match_string):
    return spec.satisfies(match_string)


def find_matching_runner(spec, ci_mappings):
    for ci_name, ci_mapping in ci_mappings.items():
        for match_string in ci_mapping['match']:
            if spec_matches(spec, match_string):
                return ci_name
    return None


def find_compiler_specs(spec_list):
    COMPILER_TO_PACKAGE_MAPPING = {
        'clang': 'llvm',
    }

    compiler_specs = []

    os_to_compilers_map = {}

    for s in spec_list:
        arch = s.architecture

        if arch not in os_to_compilers_map:
            os_to_compilers_map[arch] = []

        compilers_list = os_to_compilers_map[arch]

        if s.compiler not in compilers_list:
            compilers_list.append(s.compiler)

    for arch in os_to_compilers_map:
        compiler_list = os_to_compilers_map[arch]
        for c in compiler_list:
            compiler_pkg_name = c.name
            if c.name in COMPILER_TO_PACKAGE_MAPPING:
                compiler_pkg_name = COMPILER_TO_PACKAGE_MAPPING[c.name]
            s = Spec('{0}@{1} arch={2}'.format(
                compiler_pkg_name, c.version, arch))
            compiler_specs.append(s)

    return compiler_specs


def release_jobs(parser, args):
    env = ev.get_env(args, 'release-jobs', required=True)
    env.concretize(force=args.force)

    # FIXME: What's the difference between one that opens with 'spack'
    # and one that opens with 'env'?  This will only handle the former.
    yaml_root = env.yaml['spack']

    if 'gitlab-ci' not in yaml_root:
        tty.die('Environment yaml does not have "gitlab-ci" section')

    ci_mappings = yaml_root['gitlab-ci']['mappings']

    ci_cdash = yaml_root['cdash']
    build_group = ci_cdash['build-group']
    cdash_url = ci_cdash['url']
    cdash_project = ci_cdash['project']
    proj_enc = urlencode({'project': cdash_project})
    eq_idx = proj_enc.find('=') + 1
    cdash_project_enc = proj_enc[eq_idx:]
    cdash_site = ci_cdash['site']
    cdash_auth_token = None

    if args.cdash_credentials:
        with open(args.cdash_credentials) as fd:
            cdash_auth_token = fd.read()
            cdash_auth_token = cdash_auth_token.strip()

    ci_mirrors = yaml_root['mirrors']
    mirror_urls = ci_mirrors.values()

    # root_specs = env.all_specs()
    root_specs = [s for s in env.roots()]

    tty.msg('Your environment file represents the following specs:')
    for s in root_specs:
        print('  {0}'.format(s))

    # First map the roots specs from the environment to runners
    root_spec_runners = {}
    compiler_spec_runners = {}
    for s in root_specs:
        runner_name = find_matching_runner(s, ci_mappings)
        root_spec_str = get_spec_string(s)
        root_spec_runners[root_spec_str] = runner_name
        compiler_spec_str = '{0} arch={1}'.format(s.compiler, s.architecture)
        compiler_spec_runners[compiler_spec_str] = runner_name

    compiler_specs = find_compiler_specs(root_specs)
    compiler_spec_labels, compiler_deps, compiler_stages = stage_spec_jobs(
        compiler_specs, True)

    spec_labels, dependencies, stages = stage_spec_jobs(root_specs)

    overlap = any([elt in compiler_deps for elt in dependencies.keys()])

    if overlap:
        tty.warn('  !!! WARNING: overlap between release and compiler jobs !!!')
        duplicates = [elt for elt in dependencies.keys() if elt in compiler_deps]
        tty.warn(', '.join(duplicates))

    if not stages:
        tty.msg('No jobs staged, exiting.')
        return

    if args.print_summary:
        tty.msg('Compiler staging summary:\n')
        print_staging_summary(
            compiler_spec_labels, compiler_deps, compiler_stages)
        tty.msg('\nRelease jobs staging summary:\n')
        print_staging_summary(spec_labels, dependencies, stages)
        tty.msg('\n')

    all_job_names = []
    output_object = {}
    job_count = 0

    total_num_stages = len(compiler_stages) + len(stages)
    stage_names = ['stage-{0}'.format(i) for i in range(total_num_stages)]
    stage = 0

    def add_job(spec_label, stage_name, labels, deps, compiler_job=False):
        release_spec = labels[spec_label]['spec']
        root_spec = labels[spec_label]['rootSpec']

        if not compiler_job:
            runner_name = root_spec_runners[root_spec]
        else:
            runner_name = compiler_spec_runners[root_spec]

        if runner_name:
            runner_attrs = ci_mappings[runner_name]['runner-attributes']

        if not runner_attrs:
            tty.warn('No match found for {0}, skipping it'.format(
                release_spec))
            return False

        tags = [tag for tag in runner_attrs['tags']]

        variables = {}
        if 'variables' in runner_attrs:
            variables.update(runner_attrs['variables'])

        build_image = None
        if 'image' in runner_attrs:
            build_image = runner_attrs['image']

        spec_arch = str(release_spec.architecture)
        job_name = get_job_name(release_spec, spec_arch, build_group)
        cdash_build_name = get_cdash_build_name(release_spec, build_group)

        all_job_names.append(cdash_build_name)

        job_scripts = ['./bin/rebuild-package.sh']

        job_dependencies = []    # used by gitlab-ci for artifact finding
        related_builds = []      # Used for relating CDash builds
        if spec_label in deps:
            job_dependencies = (
                [get_job_name(labels[d]['spec'], spec_arch, build_group)
                    for d in deps[spec_label]])
            related_builds = (
                [labels[d]['spec'].name
                    for d in deps[spec_label]])

        if not compiler_job:
            job_compiler_spec = Spec('{0} arch={1}'.format(release_spec.compiler, spec_arch))
            job_dependencies.append(get_job_name(job_compiler_spec, spec_arch, build_group))

        job_variables = {
            'SPACK_MIRROR_URL': mirror_urls[0],
            'SPACK_CDASH_BASE_URL': cdash_url,
            'SPACK_CDASH_PROJECT': cdash_project,
            'SPACK_CDASH_PROJECT_ENC': cdash_project_enc,
            'SPACK_CDASH_BUILD_NAME': cdash_build_name,
            'SPACK_RELATED_BUILDS': ';'.join(related_builds),
            'SPACK_ROOT_SPEC': root_spec,
            'SPACK_JOB_SPEC_PKG_NAME': release_spec.name,
            'SPACK_JOB_SPEC_BUILDGROUP': build_group,
        }

        if not compiler_job:
            job_variables['SPACK_FIND_COMPILER'] = str(release_spec.compiler)

        variables.update(job_variables)

        job_object = {
            'stage': stage_name,
            'variables': variables,
            'script': job_scripts,
            'artifacts': {
                'paths': [
                    'local_mirror/build_cache',
                    'jobs_scratch_dir',
                    'cdash_report',
                ],
                'when': 'always',
            },
            'dependencies': job_dependencies,
            'tags': tags,
        }

        if build_image:
            job_object['image'] = build_image

        output_object[job_name] = job_object

        return True

    phases = [
        (compiler_stages, compiler_spec_labels, compiler_deps, True),
        (stages, spec_labels, dependencies, False),
    ]

    for stage_list, labels, deps, compiler_job in phases:
        for stage_jobs in stage_list:
            stage_name = stage_names[stage]

            for spec_label in stage_jobs:
                if add_job(spec_label, stage_name, labels, deps, compiler_job):
                    job_count += 1

            stage += 1

    tty.msg('{0} build jobs generated in {1} stages'.format(
        job_count, total_num_stages))

    # Use "all_job_names" to populate the build group for this set
    if cdash_auth_token:
        try:
            populate_buildgroup(all_job_names, build_group, cdash_project,
                                cdash_site, cdash_auth_token, cdash_url)
        except (SpackError, HTTPError, URLError) as err:
            tty.warn('Problem populating buildgroup: {0}'.format(err))
    else:
        tty.warn('Unable to populate buildgroup without CDash credentials')

    # Add an extra, final job to regenerate the index
    final_stage = 'stage-rebuild-index'
    final_job = {
        'stage': final_stage,
        'variables': {
            'MIRROR_URL': mirror_urls[0],
        },
        'image': 'scottwittenburg/spack_ci_generator_alpine',  # just needs some basic python image
        'script': './bin/rebuild-index.sh',
        'tags': ['spack-k8s']    # may want a runner to handle this
    }
    output_object['rebuild-index'] = final_job
    stage_names.append(final_stage)

    output_object['stages'] = stage_names

    with open(args.output_file, 'w') as outf:
        outf.write(syaml.dump(output_object))
