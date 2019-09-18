# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os
import pytest

from spack.main import SpackCommand
import spack.util.executable as exe


ci = SpackCommand('ci')
git = exe.which('git', required=True)


def test_ci_generate_and_push():
    # output = git('rev-parse', '--abbrev-ref', 'HEAD', output=str)
    # current_branch = output.split()[0]

    # output = git('rev-parse', 'HEAD', output=str)
    # current_sha = output.split()[0]

    # os.environ['SPACK_RELEASE_ENVIRONMENT_REPO'] = 'https://github.com/scottwittenburg/site-specific-release.git'
    # os.environ['SPACK_RELEASE_ENVIRONMENT_PATH'] = 'release_environment'
    # os.environ['DOWNSTREAM_CI_REPO'] = 'https://github.com/scottwittenburg/spack-test-ci.git'
    # os.environ['CI_COMMIT_REF_NAME'] = current_branch
    # os.environ['CI_COMMIT_SHA'] = current_sha

    original_wd = os.getcwd()

    try:
        ci('generate')
    except Exception as inst:
        os.chdir(original_wd)
        print('Caught exception:')
        print(inst)
        assert False == True

