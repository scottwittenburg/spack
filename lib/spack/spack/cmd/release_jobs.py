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
        '-o', '--output-file', default=".gitlab-ci.yml",
        help="path to output file to write")

    subparser.add_argument(
        '-p', '--print-summary', action='store_true', default=False,
        help="Print summary of staged jobs to standard output")

    subparser.add_argument(
        '--cdash-credentials', default=None,
        help="Path to file containing CDash authentication token")

    subparser.add_argument(
        '--use-artifacts-mirror', default=False,
        action='store_true', help="Maintain local binary mirror within " +
        "gitlab artifacts, which reduces amount of data which needs to be " +
        "downloaded from remote binary mirrors when installing binary " +
        "packages.")


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


def is_main_phase(phase_name):
    return True if phase_name == 'specs' else False


def get_job_name(phase, strip_compiler, spec, osarch, build_group):
    if is_main_phase(phase) == False and strip_compiler == True:
        return '({0}) {1} {2} {3} {4}'.format(
            phase, spec.name, spec.version, osarch, build_group)
    else:
        return '({0}) {1} {2} {3} {4} {5}'.format(
            phase, spec.name, spec.version, spec.compiler, osarch, build_group)


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
    if main_phase == False and strip_compiler == True:
        return '{0}@{1} arch={2}'.format(
            spec.name, spec.version, spec.architecture)
    else:
        return '{0}@{1}%{2} arch={3}'.format(
            spec.name, spec.version, spec.compiler, spec.architecture)


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
                'rootSpec': Spec(entry['root_spec']),
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


def compute_spec_deps(spec_list, stream_like=None):
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

        root_spec = get_spec_string(spec)

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

    if stream_like:
        stream_like.write(json.dumps(deps_json_obj))

    return deps_json_obj


def spec_matches(spec, match_string):
    return spec.satisfies(match_string)


def find_matching_config(spec, ci_mappings):
    for ci_mapping in ci_mappings:
        for match_string in ci_mapping['match']:
            if spec_matches(spec, match_string):
                return ci_mapping['runner-attributes']
    return None


def release_jobs(parser, args):
    env = ev.get_env(args, 'release-jobs', required=True)

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

    staged_phases = {
        'specs': stage_spec_jobs(env.all_specs()),
    }


    if 'phases' in yaml_root['gitlab-ci']:
        phases = []
        for phase in yaml_root['gitlab-ci']['phases']:
            try:
                phase_name = phase.get('name')
                strip_compilers = phase.get('strip-compilers')
                enable_cdash_reporting = phase.get('cdash-reporting')
            except AttributeError:
                phase_name = phase
                strip_compilers = False
                enable_cdash_reporting = True
            phases.append({
                'name': phase_name,
                'strip-compilers': strip_compilers,
                'cdash-reporting': enable_cdash_reporting,
            })
    else:
        phases = [{
            'name': 'specs',
            'strip-compilers': False,
            'cdash-reporting': True,
        }]

    for phase in phases:
        phase_name = phase['name']
        staged_phases[phase_name] = stage_spec_jobs(env.spec_lists[phase_name])

    if args.print_summary:
        for phasee in phases:
            phase_name = phase['name']
            tty.msg('Stages for phase "{0}"'.format(phase_name))
            phase_stages = staged_phases[phase_name]
            print_staging_summary(*phase_stages)

    all_job_names = []
    output_object = {}
    job_id = 0
    stage_id = 0

    stage_names = []

    for phase in phases:
        phase_name = phase['name']
        strip_compilers = phase['strip-compilers']
        enable_cdash_reporting = phase['cdash-reporting']

        main_phase = is_main_phase(phase_name)
        spec_labels, dependencies, stages = staged_phases[phase_name]

        for stage_jobs in stages:
            stage_name = 'stage-{0}'.format(stage_id)
            stage_names.append(stage_name)
            stage_id += 1

            for spec_label in stage_jobs:
                release_spec = spec_labels[spec_label]['spec']
                root_spec = spec_labels[spec_label]['rootSpec']

                runner_attribs = find_matching_config(root_spec, ci_mappings)

                if not runner_attribs:
                    tty.warn('No match found for {0}, skipping it'.format(
                        release_spec))
                    continue

                tags = [tag for tag in runner_attribs['tags']]

                variables = {}
                if 'variables' in runner_attribs:
                    variables.update(runner_attribs['variables'])

                build_image = None
                if 'image' in runner_attribs:
                    build_image = runner_attribs['image']

                osname = str(release_spec.architecture)
                job_name = get_job_name(phase_name, strip_compilers,
                                        release_spec, osname, build_group)
                cdash_build_name = get_cdash_build_name(release_spec, build_group)

                all_job_names.append(cdash_build_name)

                job_scripts = ['./bin/rebuild-package.sh']

                related_builds = []      # Used for relating CDash builds
                if spec_label in dependencies:
                    related_builds = (
                        [spec_labels[d]['spec'].name
                            for d in dependencies[spec_label]])

                compiler_action = 'NONE'
                if len(phases) > 1:
                    compiler_action = 'FIND_ANY'
                    if is_main_phase(phase_name):
                        compiler_action = 'INSTALL_MISSING'

                job_variables = {
                    'SPACK_MIRROR_URL': mirror_urls[0],
                    'SPACK_CDASH_BASE_URL': cdash_url,
                    'SPACK_CDASH_PROJECT': cdash_project,
                    'SPACK_CDASH_PROJECT_ENC': cdash_project_enc,
                    'SPACK_CDASH_BUILD_NAME': cdash_build_name,
                    'SPACK_RELATED_BUILDS': ';'.join(related_builds),
                    'SPACK_ROOT_SPEC': format_root_spec(
                        root_spec, main_phase, strip_compilers),
                    'SPACK_JOB_SPEC_PKG_NAME': release_spec.name,
                    'SPACK_JOB_SPEC_BUILDGROUP': build_group,
                    'SPACK_COMPILER_ACTION': compiler_action,
                }

                variables.update(job_variables)

                job_object = {
                    'stage': stage_name,
                    'variables': variables,
                    'script': job_scripts,
                    'tags': tags,
                }

                artifact_paths = [
                    'jobs_scratch_dir',
                    'cdash_report',
                ]

                if args.use_artifacts_mirror:
                    artifact_paths.append('local_mirror/build_cache')
                    # Here we omit 'dependencies' and allow the default
                    # behavior, which is to download artifacts from all
                    # previous stages.
                else:
                    # According to gitlab-ci docs, setting 'dependencies' to an
                    # empty array will skip downloading any artifacts for the
                    # job
                    job_object['dependencies'] = []

                job_object['artifacts'] = {
                    'paths': artifact_paths,
                    'when': 'always',
                }

                if build_image:
                    job_object['image'] = build_image

                output_object[job_name] = job_object
                job_id += 1

    tty.msg('{0} build jobs generated in {1} stages'.format(
        job_id, stage_id))

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
