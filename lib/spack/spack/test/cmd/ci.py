# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os
import pytest

import llnl.util.filesystem as fs

import spack.compilers as compilers
from spack.main import SpackCommand
import spack.util.executable as exe
import spack.util.spack_yaml as syaml


pytestmark = pytest.mark.usefixtures(
    'mutable_mock_env_path', 'config', 'mutable_mock_packages')

ci = SpackCommand('ci')
git = exe.which('git', required=True)


def initialize_new_repo(repo_path, initial_commit=False):
    if not os.path.exists(repo_path):
        os.makedirs(repo_path)

    with fs.working_dir(repo_path):
        init_args = ['init', '.']
        # if not initial_commit:
        #     init_args.append('--bare')

        git(*init_args)

        if initial_commit:
            readme_contents = "This is the project README\n"
            readme_path = os.path.join(repo_path, 'README.md')
            with open(readme_path, 'w') as fd:
                fd.write(readme_contents)
            git('add', '.')
            git('commit', '-m', 'Project initial commit')


def get_repo_status(repo_path):
    with fs.working_dir(repo_path):
        output = git('rev-parse', '--abbrev-ref', 'HEAD', output=str)
        current_branch = output.split()[0]

        output = git('rev-parse', 'HEAD', output=str)
        current_sha = output.split()[0]

        return current_branch, current_sha


def test_ci_generate(tmpdir):
    env_repo = 'https://github.com/scottwittenburg/site-specific-release.git'
    env_path = 'test_environment'

    compilers._cache_config_file = []

    jobs_yaml_file = tmpdir.join('.gitlab-ci.yml')

    generate_args = [
        'generate',
        '--output-file', jobs_yaml_file.strpath,
        '--env-repo', env_repo,
        '--env-path', env_path
    ]

    ci(*generate_args)

    with jobs_yaml_file.open('r') as f:
        contents = syaml.load(f)

    assert 'stages' in contents


def test_ci_pushyaml(tmpdir):
    fake_yaml_contents = """generate ci jobs:
  script:
    - "./share/spack/qa/gitlab/generate-gitlab-ci-yml.sh"
  tags:
    - "spack-pre-ci"
  artifacts:
    paths:
      - ci-generation
    when: always
 """
    local_repo_path = tmpdir.join('local_repo')
    initialize_new_repo(local_repo_path.strpath, True)

    remote_repo_path = tmpdir.join('remote_repo')
    initialize_new_repo(remote_repo_path.strpath)

    current_branch, current_sha = get_repo_status(local_repo_path.strpath)

    print('local repo info: {0}, {1}'.format(current_branch, current_sha))

    local_jobs_yaml = local_repo_path.join('.gitlab-ci.yml')
    with local_jobs_yaml.open('w') as f:
        f.write(fake_yaml_contents)

    pushyaml_args = [
        'pushyaml',
        '--yaml-path', local_jobs_yaml.strpath,
        '--downstream-repo', remote_repo_path.strpath,
        '--branch-name', current_branch,
        '--commit-sha', current_sha,
    ]

    ci(*pushyaml_args)

    with fs.working_dir(remote_repo_path.strpath):
        branch_to_checkout = 'multi-ci-{0}'.format(current_branch)
        git('checkout', branch_to_checkout)
        with open('.gitlab-ci.yml') as fd:
            pushed_contents = fd.read()
            assert pushed_contents == fake_yaml_contents
