# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import argparse
import os
import shutil
import sys
import tempfile

from six import iteritems
from six.moves.urllib.parse import urlencode

import llnl.util.tty as tty

from spack.main import SpackCommand
from spack.spec import Spec, save_dependency_spec_yamls
import spack.util.spack_yaml as syaml

description = "build (as necessary) a package in the release workflow"
section = "build"
level = "long"


spack_gpg = SpackCommand('gpg')
spack_buildcache = SpackCommand('buildcache')


def setup_parser(subparser):
    subparser.add_argument('--ci-project-dir', default=None,
        help="Absolute system path to spack (include 'spack' at the end)")
    subparser.add_argument('--ci-job-name', default=None,
        help="Job name from .gitlab-ci.yml")
    subparser.add_argument('--cdash-base-url', default=None,
        help="Base URL to CDash instance for reporting")
    subparser.add_argument('--cdash-project', default=None,
        help="CDash project name to use when submitting results")
    subparser.add_argument('--dependencies', default=None,
        help="Semi-colon separated list of job names this job depends on")
    subparser.add_argument('--mirror-url', default=None,
        help="Remote binary mirror url for uploading/downloading binaries")
    subparser.add_argument('--root-spec', default=None,
        help="Root spec of which this package is a dependency")


def setup_environment():
    os.environ['FORCE_UNSAFE_CONFIGURE'] = 1


def url_encode_string(input_string):
    encoded_keyval = urlencode({'donotcare': input_string})
    eq_idx = encoded_keyval.find('=') + 1
    encoded_value = encoded_keyval[eq_idx:]


def save_full_yamls(job_name, dependencies, root_spec_name, output_dir):
    parts_list = job_name.split()

    pkg_name = parts_list[0]
    pkg_version = parts_list[1]
    compiler = parts_list[2]
    os_arch = parts_list[3]
    release_tag = parts_list[4]

    job_spec_name = '{0}@{1}%{2} arch={3} ({4})'.format(
        pkg_name, pkg_version, compiler, os_arch, release_tag)
    job_group = release_tag
    spec_yaml_path = os.path.join(output_dir, '{0}.yaml'.format(pkg_name))

    job_deps_pkg_names = []

    deps_list = dependencies.split(';')
    for dep_job_name in deps_list:
        dep_parts_list = dep_job_name.split()
        dep_pkg_name = dep_parts_list[0]
        job_deps_pkg_names.append(dep_pkg_name)

    root_spec = Spec(args.root_spec)
    root_spec.concretize()
    root_spec_as_yaml = root_spec.to_yaml(all_deps=True)

    save_dependency_spec_yamls(
        root_spec_as_yaml, output_dir, job_deps_pkg_names + [pkg_name])


def rebuild_package(parser, args):
    ci_project_dir = args.ci_project_dir
    ci_job_name = args.ci_job_name
    cdash_base_url = args.cdash_base_url
    cdash_project = args.cdash_project
    dependencies = args.dependencies
    mirror_url = args.mirror_url
    root_spec = args.root_spec

    setup_environment()

    temp_dir = os.path.join(ci_project_dir, 'jobs_scratch_dir')
    job_log_dir = os.path.join(temp_dir, 'logs')
    spec_dir = os.path.join(temp_dir, 'specs')

    local_mirror_dir = os.path.join(ci_project_dir, 'local_mirror')
    build_cache_dir = os.path.join(local_mirror_dir, 'build_cache')

    cdash_project_encoded = url_encode_string(cdash_project)
    cdash_upload_url = '{0}/submit.php?project={1}'.format(
        cdash_base_url, cdash_project_encoded)
    dep_job_relatebuilds_url = '{0}/api/v1/relateBuilds.php'.format(
        cdash_base_url)

    os.makedirs(job_log_dir)
    os.makedirs(spec_dir)

    job_log_file = os.path.join(job_log_dir, 'cdash_log.txt')

    with open(job_log_file, 'w') as fd:
        os.dup2(fd.fileno(), sys.stdout.fileno())
        os.dup2(fd.fileno(), sys.stderr.fileno())



    """

gen_full_specs_for_job_and_deps

echo "Building package ${JOB_SPEC_NAME}, ${HASH}, ${MIRROR_URL}"

# Finally, list the compilers spack knows about
echo "Compiler Configurations:"
spack config get compilers

# Make the build_cache directory if it doesn't exist
mkdir -p "${BUILD_CACHE_DIR}"

# Get buildcache name so we can write a CDash build id file in the right place.
# If we're unable to get the buildcache name, we may have encountered a problem
# concretizing the spec, or some other issue that will eventually cause the job
# to fail.
JOB_BUILD_CACHE_ENTRY_NAME=`spack -d buildcache get-buildcache-name --spec-yaml "${SPEC_YAML_PATH}"`
if [[ $? -ne 0 ]]; then
    echo "ERROR, unable to get buildcache entry name for job ${CI_JOB_NAME} (spec: ${JOB_SPEC_NAME})"
    exit 1
fi

# This should create the directory we referred to as GNUPGHOME earlier
spack gpg list

# Importing the secret key using gpg2 directly should allow to
# sign and verify both
set +x
KEY_IMPORT_RESULT=`echo ${SPACK_SIGNING_KEY} | base64 --decode | gpg2 --import`
check_error $? "gpg2 --import"
set -x

spack gpg list --trusted
spack gpg list --signing

# Whether we have to build the spec or download it pre-built, we expect to find
# the cdash build id file sitting in this location afterwards.
JOB_CDASH_ID_FILE="${BUILD_CACHE_DIR}/${JOB_BUILD_CACHE_ENTRY_NAME}.cdashid"

# Finally, we can check the spec we have been tasked with build against
# the built binary on the remote mirror to see if it needs to be rebuilt
spack -d buildcache check --spec-yaml "${SPEC_YAML_PATH}" --mirror-url "${MIRROR_URL}" --rebuild-on-error

if [[ $? -ne 0 ]]; then
    # Configure mirror
    spack mirror add local_artifact_mirror "file://${LOCAL_MIRROR}"

    JOB_CDASH_ID="NONE"

    # Install package, using the buildcache from the local mirror to
    # satisfy dependencies.
    BUILD_ID_LINE=`spack -d -k -v install --use-cache --keep-stage --cdash-upload-url "${CDASH_UPLOAD_URL}" --cdash-build "${JOB_SPEC_NAME}" --cdash-site "Spack AWS Gitlab Instance" --cdash-track "${JOB_GROUP}" -f "${SPEC_YAML_PATH}" | grep "buildSummary\\.php"`
    check_error $? "spack install"

    # Copy some log files into an artifact location
    stage_dir=$(spack location --stage-dir -f "${SPEC_YAML_PATH}")
    build_log_file=$(find -L "${stage_dir}" | grep "spack-build\\.out")
    config_log_file=$(find -L "${stage_dir}" | grep "config\\.log")
    cp "${build_log_file}" "${JOB_LOG_DIR}/"
    cp "${config_log_file}" "${JOB_LOG_DIR}/"

    # By parsing the output of the "spack install" command, we can get the
    # buildid generated for us by CDash
    JOB_CDASH_ID=$(extract_build_id "${BUILD_ID_LINE}")

    # Create buildcache entry for this package, reading the spec from the yaml
    # file.
    spack -d buildcache create --spec-yaml "${SPEC_YAML_PATH}" -a -f -d "${LOCAL_MIRROR}" --no-rebuild-index
    check_error $? "spack buildcache create"

    # Write the .cdashid file to the buildcache as well
    echo "${JOB_CDASH_ID}" >> ${JOB_CDASH_ID_FILE}

    # TODO: The upload-s3 command should eventually be replaced with something
    # like: "spack buildcache put <mirror> <spec>", when that subcommand is
    # properly implemented.
    spack -d upload-s3 spec --base-dir "${LOCAL_MIRROR}" --spec-yaml "${SPEC_YAML_PATH}"
    check_error $? "spack upload-s3 spec"
else
    echo "spec ${JOB_SPEC_NAME} is already up to date on remote mirror, downloading it"

    # Configure remote mirror so we can download buildcache entry
    spack mirror add remote_binary_mirror ${MIRROR_URL}

    # Now download it
    spack -d buildcache download --spec-yaml "${SPEC_YAML_PATH}" --path "${BUILD_CACHE_DIR}/" --require-cdashid
    check_error $? "spack buildcache download"
fi

# The next step is to relate this job to the jobs it depends on
if [ -f "${JOB_CDASH_ID_FILE}" ]; then
    JOB_CDASH_BUILD_ID=$(<${JOB_CDASH_ID_FILE})

    if [ "${JOB_CDASH_BUILD_ID}" == "NONE" ]; then
        echo "ERROR: unable to read this jobs id from ${JOB_CDASH_ID_FILE}"
        exit 1
    fi

    # Now get CDash ids for dependencies and "relate" each dependency build
    # with this jobs build
    for DEP_PKG_NAME in "${JOB_DEPS_PKG_NAMES[@]}"; do
        echo "Getting cdash id for dependency --> ${DEP_PKG_NAME} <--"
        DEP_SPEC_YAML_PATH="${SPEC_DIR}/${DEP_PKG_NAME}.yaml"
        DEP_JOB_BUILDCACHE_NAME=`spack -d buildcache get-buildcache-name --spec-yaml "${DEP_SPEC_YAML_PATH}"`

        if [[ $? -eq 0 ]]; then
            DEP_JOB_ID_FILE="${BUILD_CACHE_DIR}/${DEP_JOB_BUILDCACHE_NAME}.cdashid"
            echo "DEP_JOB_ID_FILE path = ${DEP_JOB_ID_FILE}"

            if [ -f "${DEP_JOB_ID_FILE}" ]; then
                DEP_JOB_CDASH_BUILD_ID=$(<${DEP_JOB_ID_FILE})
                echo "File ${DEP_JOB_ID_FILE} contained value ${DEP_JOB_CDASH_BUILD_ID}"
                echo "Relating builds -> ${JOB_SPEC_NAME} (buildid=${JOB_CDASH_BUILD_ID}) depends on ${DEP_PKG_NAME} (buildid=${DEP_JOB_CDASH_BUILD_ID})"
                relateBuildsPostBody="$(get_relate_builds_post_data "${CDASH_PROJECT}" ${JOB_CDASH_BUILD_ID} ${DEP_JOB_CDASH_BUILD_ID})"
                relateBuildsResult=`curl "${DEP_JOB_RELATEBUILDS_URL}" -H "Content-Type: application/json" -H "Accept: application/json" -d "${relateBuildsPostBody}"`
                echo "Result of curl request: ${relateBuildsResult}"
            else
                echo "ERROR: Did not find expected .cdashid file for dependency: ${DEP_JOB_ID_FILE}"
                exit 1
            fi
        else
            echo "ERROR: Unable to get buildcache entry name for ${DEP_SPEC_NAME}"
            exit 1
        fi
    done
else
    echo "ERROR: Did not find expected .cdashid file ${JOB_CDASH_ID_FILE}"
    exit 1
fi

# Show the size of the buildcache and a list of what's in it, directly
# in the gitlab log output
(
    restore_io
    du -sh ${BUILD_CACHE_DIR}
    find ${BUILD_CACHE_DIR} -maxdepth 3 -type d -ls
)

echo "End of rebuild package script"



check_error()
{
    local last_exit_code=$1
    local last_cmd=$2
    if [[ ${last_exit_code} -ne 0 ]]; then
        echo "${last_cmd} exited with code ${last_exit_code}"
        echo "TERMINATING JOB"
        exit 1
    else
        echo "${last_cmd} completed successfully"
    fi
}

extract_build_id()
{
    LINES_TO_SEARCH=$1
    regex="buildSummary\.php\?buildid=([[:digit:]]+)"
    SINGLE_LINE_OUTPUT=$(echo ${LINES_TO_SEARCH} | tr -d '\n')

    if [[ ${SINGLE_LINE_OUTPUT} =~ ${regex} ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo "NONE"
    fi
}

get_relate_builds_post_data()
{
  cat <<EOF
{
  "project": "${1}",
  "buildid": ${2},
  "relatedid": ${3},
  "relationship": "depends on"
}
EOF
}



    """
