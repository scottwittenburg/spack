# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os
import shutil
import sys
import tempfile

import llnl.util.tty as tty

import spack.cmd.release_jobs as release_jobs
import spack.util.executable as exe


description = "Implement various pieces of CI pipeline for spack"
section = "packaging"     # TODO: where does this go?  new section?
level = "long"


# We can get rid of this when we no longer need to support Python 2, and
# at that point we will use tempfile.TemporaryDirectory, after which this
# was modeled.
class TemporaryDirectory(object):
    def __init__(self):
        self.temporary_directory = tempfile.mkdtemp()

    def __enter__(self):
        return self.temporary_directory

    def __exit__(self, exc_type, exc_value, exc_traceback):
        shutil.rmtree(self.temporary_directory)
        return False


def get_env_var(variable_name):
    if variable_name in os.environ:
        return os.environ[variable_name]
    return None


def setup_parser(subparser):
    setup_parser.parser = subparser
    subparsers = subparser.add_subparsers(help='CI sub-commands')

    generate = subparsers.add_parser('generate', help=ci_generate.__doc__)
    generate.set_defaults(func=ci_generate)


def ci_generate(args):
    """ Generate .gitlab-ci.yml file, create a commit with it, and
    push the commit somewhere """

    # Collect environment variables
    env_repo = get_env_var('SPACK_RELEASE_ENVIRONMENT_REPO')
    env_path = get_env_var('SPACK_RELEASE_ENVIRONMENT_PATH')
    downstream_ci_repo = get_env_var('DOWNSTREAM_CI_REPO')
    cdash_auth_token = get_env_var('CDASH_AUTH_TOKEN')
    current_branch = get_env_var('CI_COMMIT_REF_NAME')
    ci_commit_sha = get_env_var('CI_COMMIT_SHA')

    if not downstream_ci_repo:
        tty.error('ERROR: variable DOWNSTREAM_CI_REPO is required')
        sys.exit(1)

    if not env_path:
        tty.error('ERROR: variable SPACK_RELEASE_ENVIRONMENT_PATH is required')
        sys.exit(1)

    original_directory = os.getcwd()
    token_file = None
    git = exe.which('git', required=True)

    # Create a temporary working directory
    with TemporaryDirectory() as temp_dir:
        # Write cdash auth token to file system
        if cdash_auth_token:
            token_file = os.path.join(temp_dir, cdash_auth_token)
            with open(token_file, 'w') as fd:
                fd.write('{0}\n'.format(cdash_auth_token))

        # Either spack repo contains environment file, or we need to clone
        # the repo where it lives.
        if not env_repo:
            env_repo_dir = original_directory
        else:
            os.chdir(temp_dir)
            clone_args = ['clone', env_repo, 'envrepo']
            git(*clone_args, output=str)
            env_repo_dir = os.path.join(temp_dir, 'envrepo')
            os.chdir(env_repo_dir)

        # If we want to see the generated gitlab-ci file as an artifact,
        # we need to write it within the spack repo cloned by gitlab-ci.
        gen_ci_dir = os.path.join(original_directory, 'ci-generation')
        gen_ci_file = os.path.join(gen_ci_dir, '.gitlab-ci.yml')

        if not os.path.exists(gen_ci_dir):
            os.makedirs(gen_ci_dir)

        env_dir = os.path.join(env_repo_dir, env_path)
        spack_yaml_path = os.path.join(env_dir, 'spack.yaml')

        if not os.path.exists(spack_yaml_path):
            tty.error('ERROR: Cannot find "spack.yaml" file in {0}'.format(
                env_dir))
            sys.exit(1)

        os.chdir(env_dir)

        tty.msg('Now sitting in {0}, contents:'.format(os.getcwd()))
        tty.msg(os.listdir(os.getcwd()))

        # Generate the .gitlab-ci.yml, and optionally create a buildgroup in
        # cdash.
        release_jobs_args = [gen_ci_file, False]
        if token_file:
            release_jobs_args.append(token_file)

        release_jobs.generate_jobs(*release_jobs_args)

        # Push a commit with the generated file to the downstream ci repo
        saved_git_dir = os.path.join(temp_dir, 'original-git-dir')
        os.chdir(original_directory)
        shutil.move('.git', saved_git_dir)

        git('init', '.')

        git('config', 'user.email', 'robot@spack.io')
        git('config', 'user.name', 'Spack Build Bot')

        yaml_to_commit = os.path.join(original_directory, '.gitlab-ci.yml')
        shutil.copyfile(gen_ci_file, yaml_to_commit)
        git('add', '.')

        tty.msg('git commit')
        commit_message = '{0} {1} ({2})'.format(
            'Auto-generated commit testing', current_branch, ci_commit_sha)

        git('commit', '-m', '{0}'.format(commit_message))

        tty.msg('git push')
        git('remote', 'add', 'origin', downstream_ci_repo)
        push_to_branch = 'master:multi-ci-{0}'.format(current_branch)
        git('push', '--force', 'origin', push_to_branch)

        shutil.rmtree('.git')
        shutil.move(saved_git_dir, '.git')
        git('reset', '--hard', 'HEAD')


def ci(parser, args):
    if args.func:
        args.func(args)
