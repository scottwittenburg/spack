# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import argparse
import base64
import datetime
import json
import os
import shutil
import sys
from subprocess import Popen, PIPE
import tempfile
import zlib

from six import iteritems
from six.moves.urllib.parse import urlencode
from six.moves.urllib.request import build_opener, HTTPHandler, Request

import llnl.util.tty as tty

import spack.binary_distribution as bindist
import spack.cmd.buildcache as buildcache
import spack.config as cfg
from spack.error import SpackError
import spack.hash_types as ht
from spack.main import SpackCommand
from spack.spec import Spec, save_dependency_spec_yamls
import spack.util.spack_yaml as syaml
import spack.util.web as web_util

description = "build (as necessary) a package in the release workflow"
section = "build"
level = "long"


spack_gpg = SpackCommand('gpg')
spack_compiler = SpackCommand('compiler')
spack_buildcache = SpackCommand('buildcache')
# spack_config = SpackCommand('config')
spack_mirror = SpackCommand('mirror')
spack_install = SpackCommand('install')


class TemporaryDirectory(object):
    def __init__(self):
        self.temporary_directory = tempfile.mkdtemp()

    def __enter__(self):
        return self.temporary_directory

    def __exit__(self, exc_type, exc_value, exc_traceback):
        shutil.rmtree(self.temporary_directory)
        return False


def setup_parser(subparser):
    pass


def get_env_var(variable_name):
    if variable_name in os.environ:
        return os.environ[variable_name]
    return None


def url_encode_string(input_string):
    encoded_keyval = urlencode({'donotcare': input_string})
    eq_idx = encoded_keyval.find('=') + 1
    encoded_value = encoded_keyval[eq_idx:]


def import_signing_key(base64_signing_key):
    if not base64_signing_key:
        tty.die('No key found for signing/verifying packages')

    tty.msg('hello from import_signing_key')

    # This command has the side-effect of creating the directory referred
    # to as GNUPGHOME in setup_environment()
    list_output = spack_gpg('list', output=str)

    tty.msg('spack gpg list:')
    tty.msg(list_output)

    # Importing the secret key using gpg2 directly should allow both
    # signing and verification
    gpg_process = Popen(["gpg2", "--import"], stdin=PIPE)
    decoded_key = base64.b64decode(base64_signing_key)
    gpg_out, gpg_err = gpg_process.communicate(decoded_key)

    if gpg_out:
        tty.msg('gpg2 output: {0}'.format(gpg_out))

    if gpg_err:
        tty.msg('gpg2 error: {0}'.format(gpg_err))

    # Now print the keys we have for verifying and signing
    trusted_keys_output = spack_gpg('list', '--trusted', output=str)
    signing_keys_output = spack_gpg('list', '--signing' ,output=str)

    tty.msg('spack list --trusted')
    tty.msg(trusted_keys_output)
    tty.msg('spack list --signing')
    tty.msg(signing_keys_output)


def configure_compilers(compiler_action):
    if compiler_action == 'INSTALL_MISSING':
        tty.msg('Make sure bootstrapped compiler will be installed')
        config = cfg.get('config')
        config['install_missing_compilers'] = True
        cfg.set('config', config)
    elif compiler_action == 'FIND_ANY':
        tty.msg('Just find any available compiler')
        output = spack_compiler('find')
        tty.msg('spack compiler find')
        tty.msg(output)
    else:
        tty.msg('No compiler action to be taken')


def get_concrete_specs(root_spec, job_name, related_builds, compiler_action):
    spec_map = {
        'root': None,
        'job': None,
        'deps': {},
    }

    if compiler_action == 'FIND_ANY':
        # This corresponds to a bootstrapping phase where we need to
        # rely on any available compiler to build the package (i.e. the
        # compiler needed to be stripped from the spec when we generated
        # the job), and thus we need to concretize the root spec again.
        concrete_root = Spec(root_spec).concretized()
    else:
        # in this case, either we're relying on Spack to install missing
        # compiler bootstrapped in a previous phase, or else we only had one
        # phase (like a site which already knows what compilers are available
        # on it's runners), so we don't want to concretize that root spec
        # again.  The reason we take this path in the first case (bootstrapped
        # compiler), is that we can't concretize a spec at this point if we're
        # going to ask spack to "install_missing_compilers".
        concrete_root = Spec.from_yaml(
            str(zlib.decompress(base64.b64decode(root_spec)).decode('utf-8')))

    spec_map['root'] = concrete_root
    spec_map[job_name] = concrete_root[job_name]

    for dep_job_name in related_builds:
        spec_map['deps'][dep_job_name] = concrete_root[dep_job_name]

    return spec_map


def register_cdash_build(build_name, base_url, project, site, track):
    url = base_url + '/api/v1/addBuild.php'
    time_stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M')
    build_stamp = '{0}-{1}'.format(time_stamp, track)
    payload = {
        "project": project,
        "site": site,
        "name": build_name,
        "stamp": build_stamp,
    }
    enc_data = json.dumps(payload).encode('utf-8')

    headers = {
        'Content-Type': 'application/json',
    }

    opener = build_opener(HTTPHandler)

    request = Request(url, data=enc_data, headers=headers)

    response = opener.open(request)
    response_code = response.getcode()

    if response_code != 200 and response_code != 201:
        msg = 'Adding build failed (response code = {0}'.format(response_code)
        raise SpackError(msg)

    response_text = response.read()
    response_json = json.loads(response_text)
    build_id = response_json['buildid']

    return (build_id, build_stamp)

    """
    url = 'http://localhost/CDash/api/v1/addBuild.php'

    # Use this API endpoint to initialize a new build.
    payload = {
      "project": "MyProject",
      "site": "localhost",
      "name": "MyBuild",
      "stamp": "20180717-0100-Experimental"
    }
    r = requests.post(url, data = payload)

    201: {"buildid":"269"}

    # Repeat this request.
    # Status is 200 instead of 201 since the build already existed.
    r = requests.post(url, data = payload)
    200: {"buildid":"269"}
    # Verify that required parameters are set.
    r = requests.post(url, data = {})

    400: {"error":"Valid project required"}
    r = requests.post(url, data = {"project": "MyProject"})

    400: {"error":"Valid site required"}

    r = requests.post(url, data = {"project": "MyProject", "site": "localhost"})

    400: {"error":"Valid name required"}

    r = requests.post(url, data = {"project": "MyProject", "site": "localhost", "name": "MyBuild"})

    400: {"error":"Valid stamp required"}

    # Attempt to post to a private project without a valid bearer token.
    r = requests.post(url, data = {"project": "MyPrivateProject", ...})

    401
    """
    return build_stamp


def relate_cdash_builds(spec_map, cdash_api_url, job_build_id, cdash_project,
                        cdashids_mirror_url):
    dep_map = spec_map['deps']

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }

    for dep_pkg_name in dep_map:
        tty.msg('Fetching cdashid file for {0}'.format(dep_pkg_name))
        dep_spec = dep_map[dep_pkg_name]
        dep_build_id = read_cdashid_from_mirror(dep_spec, cdashids_mirror_url)

        payload = {
            "project": cdash_project,
            "buildid": job_build_id,
            "relatedid": dep_build_id,
            "relationship": "depends on"
        }

        enc_data = json.dumps(payload).encode('utf-8')

        opener = build_opener(HTTPHandler)

        request = Request(cdash_api_url, data=enc_data, headers=headers)

        response = opener.open(request)
        response_code = response.getcode()

        if response_code != 200 and response_code != 201:
            msg = 'Relate builds ({0} -> {1}) failed (resp code = {2})'.format(
                job_build_id, dep_build_id, response_code)
            raise SpackError(msg)

        response_text = response.read()
        tty.msg('Relate builds response: {0}'.format(response_text))


def write_cdashid_to_mirror(cdashid, spec, mirror_url):
    if not spec.concrete:
        tty.die('Can only write cdashid for concrete spec to mirror')

    with TemporaryDirectory() as tmpdir:
        local_cdash_path = os.path.join(tmpdir, 'job.cdashid')
        with open(local_cdash_path, 'w') as fd:
            fd.write(cdashid)

        buildcache_name = bindist.tarball_name(spec, '')
        cdashid_file_name = '{0}.cdashid'.format(buildcache_name)
        url = os.path.join(
            mirror_url, bindist.build_cache_relative_path(), cdashid_file_name)

        local_url = 'file://{0}'.format(local_cdash_path)
        web_util.push_to_url(local_url, mirror_url)


def read_cdashid_from_mirror(spec, mirror_url):
    if not spec.concrete:
        tty.die('Can only read cdashid for concrete spec from mirror')

    buildcache_name = bindist.tarball_name(spec, '')
    cdashid_file_name = '{0}.cdashid'.format(buildcache_name)
    url = os.path.join(
        mirror_url, bindist.build_cache_relative_path(), cdashid_file_name)

    respUrl, respHeaders, response = web_util.read_from_url(url)
    contents = response.fp.read()

    return int(contents)


def rebuild_package(parser, args):
    """ This command represents a gitlab-ci job, corresponding to a single
    release spec.  As such it must first decide whether or not the spec it
    has been assigned to build (represented by args.ci_job_name) is up to
    date on the remote binary mirror.  If it is not (i.e. the full_hash of
    the spec as computed locally does not match the one stored in the
    metadata on the mirror), this script will build the package, create a
    binary cache for it, and then push all related files to the remote binary
    mirror.  This script also communicates with a remote CDash instance to
    share status on the package build process. """

    ci_project_dir = get_env_var('CI_PROJECT_DIR')
    ci_job_name = get_env_var('CI_JOB_NAME')
    signing_key = get_env_var('SPACK_SIGNING_KEY')
    enable_cdash = get_env_var('SPACK_ENABLE_CDASH')
    root_spec = get_env_var('SPACK_ROOT_SPEC')
    remote_mirror_url = get_env_var('SPACK_MIRROR_URL')
    enable_artifacts_mirror = get_env_var('SPACK_ENABLE_ARTIFACTS_MIRROR')
    job_spec_pkg_name = get_env_var('SPACK_JOB_SPEC_PKG_NAME')
    compiler_action = get_env_var('SPACK_COMPILER_ACTION')
    cdash_base_url = get_env_var('SPACK_CDASH_BASE_URL')
    cdash_project = get_env_var('SPACK_CDASH_PROJECT')
    cdash_project_enc = get_env_var('SPACK_CDASH_PROJECT_ENC')
    cdash_build_name = get_env_var('SPACK_CDASH_BUILD_NAME')
    cdash_site = get_env_var('SPACK_CDASH_SITE')
    related_builds = get_env_var('SPACK_RELATED_BUILDS')
    job_spec_buildgroup = get_env_var('SPACK_JOB_SPEC_BUILDGROUP')

    os.environ['FORCE_UNSAFE_CONFIGURE'] = '1'
    os.environ['GNUPGHOME'] = '{0}/opt/spack/gpg'.format(ci_project_dir)

    # The following environment variables should have been provided by the CI
    # infrastructre (or some other external source).  The AWS keys are
    # used to upload buildcache entries to S3 using the boto3 api.  We import
    # the SPACK_SIGNING_KEY using the "gpg2 --import" command, it is used both
    # for verifying dependency buildcache entries and signing the buildcache
    # entry we create for our target pkg.
    #
    # AWS_ACCESS_KEY_ID
    # AWS_SECRET_ACCESS_KEY
    # AWS_ENDPOINT_URL (only needed for non-AWS S3 implementations)
    # SPACK_SIGNING_KEY

    temp_dir = os.path.join(ci_project_dir, 'jobs_scratch_dir')
    job_log_dir = os.path.join(temp_dir, 'logs')
    spec_dir = os.path.join(temp_dir, 'specs')

    local_mirror_dir = os.path.join(ci_project_dir, 'local_mirror')
    build_cache_dir = os.path.join(local_mirror_dir, 'build_cache')

    artifact_mirror_url = 'file://' + local_mirror_dir

    os.makedirs(job_log_dir)
    os.makedirs(spec_dir)

    job_spec_yaml_path = os.path.join(spec_dir, '{0}.yaml'.format())
    job_log_file = os.path.join(job_log_dir, 'cdash_log.txt')

    cdash_build_id = None
    cdash_build_stamp = None

    with open(job_log_file, 'w') as log_fd:
        os.dup2(log_fd.fileno(), sys.stdout.fileno())
        os.dup2(log_fd.fileno(), sys.stderr.fileno())

        import_signing_key(signing_key)

        configure_compilers(compiler_action)

        spec_map = get_concrete_specs(
            root_spec, job_spec_pkg_name, related_builds, compiler_action)

        job_spec = spec_map[job_spec_pkg_name]
        with open(job_spec_yaml_path) as fd:
            fd.write(job_spec.to_yaml(hash=ht.build_hash))

        if bindist.needs_rebuild(job_spec, remote_mirror_url, True):
            # Binary on remote mirror is not up to date, we need to rebuild
            # it.
            #
            # 1) add "local artifact mirror" (if enabled), or else add the
            #      remote binary mirror.  This is where dependencies should
            #      be installed from.
            if enable_artifacts_mirror:
                spack_mirror('add', 'local_mirror', artifact_mirror_url)
            else:
                spack_mirror('add', 'remote_mirror', 'remote_mirror_url')

            # 2) build up install arguments
            install_args = ['--keep-stage']

            # 3) create/register a new build on CDash (if enabled)
            if enable_cdash:
                cdash_build_id, cdash_build_stamp = register_cdash_build(
                    job_spec_pkg_name, cdash_base_url, cdash_project,
                    cdash_site, job_spec_buildgroup)

                cdash_upload_url = '{0}/submit.php?project={1}'.format(
                    cdash_base_url, cdash_project_enc)

                install_args.extend([
                    '--cdash-upload-url', cdash_upload_url,
                    '--cdash-build', cdash_build_name,
                    '--cdash-site', 'cdash_site',
                    '--cdash-stamp', cdash_build_stamp,
                ])

            install_args.extend('-f', job_spec_yaml_path)

            spack_install(*install_args)

            # 4) create buildcache on remote mirror
            spack_buildcache('create', '--spec-yaml', job_spec_yaml_path, '-a',
                '-f', '-d', remote_mirror_url, '--no-rebuild-index')

            if enable_cdash:
                write_cdashid_to_mirror(
                    cdash_build_id, job_spec, remote_mirror_url)

            # 5) create another copy of that buildcache on "local artifact
            # mirror" (if enabled)
            if enable_artifacts_mirror:
                spack_buildcache('create', '--spec-yaml', job_spec_yaml_path,
                    '-a', '-f', '-d', artifact_mirror_url,
                    '--no-rebuild-index')

                if enable_cdash:
                    write_cdashid_to_mirror(
                        cdash_build_id, job_spec, artifact_mirror_url)

            # 6) relate this build to its dependencies on CDash (if enabled)
            if enable_cdash:
                mirror_url = remote_mirror_url
                if enable_artifacts_mirror:
                    mirror_url = artifact_mirror_url
                post_url = '{0}/api/v1/relateBuilds.php'.format(cdash_base_url)
                relate_cdash_builds(
                    spec_map, post_url, cdash_build_id, cdash_project,
                    mirror_url)
        else:
            # There is nothing to do here unless "local artifact mirror" is
            # enabled, in which case, we need to download the buildcache to
            # the local artifacts directory to be used by dependent jobs in
            # subsequent stages
            if enable_artifacts_mirror:
                buildcache.download_buildcache_files(job_spec, remote_mirror_url)
