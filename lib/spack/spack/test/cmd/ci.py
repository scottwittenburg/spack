# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os

# import llnl.util.filesystem as fs

from spack.main import SpackCommand
import spack.util.executable as exe
import spack.util.spack_yaml as syaml


ci = SpackCommand('ci')
git = exe.which('git', required=True)


def test_ci_generate(tmpdir):
    env_repo = 'https://github.com/scottwittenburg/site-specific-release.git'
    env_path = 'release_environment'

    jobs_yaml_file = tmpdir.join('.gitlab-ci.yml')

    generate_args = [
        'generate',
        '--output-file', jobs_yaml_file.strpath,
        '--env-repo', env_repo,
        '--env-path', env_path
    ]

    original_wd = os.getcwd()

    try:
        ci(*generate_args)
    except Exception as inst:
        os.chdir(original_wd)
        print('Caught exception:')
        print(inst)
        assert False is True

    with jobs_yaml_file.open('r') as f:
        contents = syaml.load(f)

    assert 'stages' in contents


# def test_ci_pushyaml(tmpdir):
#     fake_yaml_contents = """generate ci jobs:
#   script:
#     - "./share/spack/qa/gitlab/generate-gitlab-ci-yml.sh"
#   tags:
#     - "spack-pre-ci"
#   artifacts:
#     paths:
#       - ci-generation
#     when: always
#  """
#     spack_root = os.environ['SPACK_ROOT']

#     original_wd = os.getcwd()

#     local_repo_path = tmpdir.join('local_repo')
#     initialize_new_repo(local_repo_path.strpath, True)

#     remote_repo_path = tmpdir.join('remote_repo')
#     initialize_new_repo(remote_repo_path.strpath)

#     current_branch, current_sha = get_repo_head_info(local_repo_path.strpath)

#     print('local repo info: {0}, {1}'.format(current_branch, current_sha))

#     assert False is True
