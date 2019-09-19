# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os
import shutil
import sys
import tempfile

import llnl.util.filesystem as fs
import llnl.util.tty as tty

import spack.cmd.release_jobs as release_jobs
import spack.util.executable as exe


description = "Implement various pieces of CI pipeline for spack"
section = "packaging"     # TODO: where does this go?  new section?
level = "long"


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


def find_nearest_repo_ancestor(somepath):
    if os.path.isdir(somepath):
        if '.git' in os.listdir(somepath):
            return somepath

        if somepath == '/':
            return None

    parent_path = os.path.dirname(somepath)

    return find_nearest_repo_ancestor(parent_path)


def setup_parser(subparser):
    setup_parser.parser = subparser
    subparsers = subparser.add_subparsers(help='CI sub-commands')

    # Dynamic generation of the jobs yaml from a spack environment
    generate = subparsers.add_parser('generate', help=ci_generate.__doc__)
    generate.add_argument(
        '--output-file', default=None,
        help="Absolute path to file where generated jobs file should be " +
             "written.  The default is ${SPACK_ROOT}/.gitlab-ci.yml")
    generate.add_argument(
        '--env-repo', default=None,
        help="Url to repository where environment file lives.  The default " +
             "is the local spack repo.")
    generate.add_argument(
        '--env-path', default='',
        help="Relative path to location of spack.yaml environment file, " +
             "where path is relative to root of environment repository.  " +
             "The default is the empty string, indicating the file lives at " +
             "the root of the repository.")
    generate.add_argument(
        '--cdash-token', default=None,
        help="Token to use for registering a (possibly new) buildgroup with " +
             "CDash, assuming the spack ci environment file includes " +
             "reporting to one or more CDash instances.  The default is " +
             "None, which prevents CDash build group registration.")
    generate.add_argument(
        '--copy-to', default=None,
        help="Absolute path of additional location where generated jobs " +
             "yaml file should be copied.  Default is not to copy.")
    generate.set_defaults(func=ci_generate)

    # Commit and push jobs yaml to a downstream CI repo
    pushyaml = subparsers.add_parser('pushyaml', help=ci_pushyaml.__doc__)
    pushyaml.add_argument(
        '--yaml-path', default=None,
        help="Absolute path to jobs yaml file, the default value is " +
             "${SPACK_ROOT}/.gitlab-ci.yml")
    pushyaml.add_argument(
        '--downstream-repo', default=None,
        help="Url to repository where commit containing jobs yaml file " +
             "should be pushed.")
    pushyaml.add_argument(
        '--branch-name', default='default-branch',
        help="Name of current branch, used in generation of pushed commit.")
    pushyaml.add_argument(
        '--commit-sha', default='none',
        help="SHA of current commit, used in generation of pushed commit.")
    pushyaml.set_defaults(func=ci_pushyaml)


def ci_generate(args):
    """Generate jobs file from a spack environment file containing CI info"""
    spack_root = get_env_var('SPACK_ROOT')

    output_file = args.output_file
    env_repo = args.env_repo
    env_path = args.env_path
    cdash_auth_token = args.cdash_token
    copy_yaml_to = args.copy_to

    if not output_file:
        output_file = os.path.join(spack_root, '.gitlab-ci.yml')

    # Create a temporary working directory
    with TemporaryDirectory() as temp_dir:
        # Write cdash auth token to file system
        token_file = None
        if cdash_auth_token:
            token_file = os.path.join(temp_dir, cdash_auth_token)
            with open(token_file, 'w') as fd:
                fd.write('{0}\n'.format(cdash_auth_token))

        # Either spack repo contains environment file, or we need to clone
        # the repo where it lives.
        if not env_repo:
            env_repo_dir = spack_root
        else:
            git = exe.which('git', required=True)
            with fs.working_dir(temp_dir):
                clone_args = ['clone', env_repo, 'envrepo']
                git(*clone_args)
            env_repo_dir = os.path.join(temp_dir, 'envrepo')

        gen_ci_dir = os.path.dirname(output_file)
        if not os.path.exists(gen_ci_dir):
            os.makedirs(gen_ci_dir)

        abs_env_dir = os.path.join(env_repo_dir, env_path)
        spack_yaml_path = os.path.join(abs_env_dir, 'spack.yaml')

        if not os.path.exists(spack_yaml_path):
            tty.error('ERROR: Cannot find "spack.yaml" file in {0}'.format(
                abs_env_dir))
            sys.exit(1)

        with fs.working_dir(abs_env_dir):
            # Generate the jobs yaml file, optionally creating a buildgroup in
            # cdash at the same time.
            release_jobs_args = [output_file, False]
            if token_file:
                release_jobs_args.append(token_file)

            release_jobs.generate_jobs(*release_jobs_args)

        if copy_yaml_to:
            copy_to_dir = os.path.dirname(copy_yaml_to)
            if not os.path.exists(copy_to_dir):
                os.makedirs(copy_to_dir)
            shutil.copyfile(output_file, copy_yaml_to)


def ci_pushyaml(args):
    """Push the generated jobs yaml file to a remote repository"""
    spack_root = get_env_var('SPACK_ROOT')

    downstream_repo = args.downstream_repo
    branch_name = args.branch_name
    commit_sha = args.commit_sha
    jobs_yaml = args.yaml_path

    if not downstream_repo:
        tty.error('No downstream repo to push to, exiting')
        sys.exit(1)

    if not jobs_yaml:
        jobs_yaml = os.path.join(spack_root, '.gitlab-ci.yml')

    # Create a temporary working directory
    with TemporaryDirectory() as temp_dir:
        repo_root = find_nearest_repo_ancestor(jobs_yaml)

        if not repo_root:
            msg = '{0} not in a git repo, cannot commit/push it'.format(
                jobs_yaml)
            tty.error(msg)
            sys.exit(1)

        git = exe.which('git', required=True)

        # Push a commit with the generated file to the downstream ci repo
        saved_git_dir = os.path.join(temp_dir, 'original-git-dir')

        with fs.working_dir(repo_root):
            shutil.move('.git', saved_git_dir)

            git('init', '.')

            git('config', 'user.email', 'robot@spack.io')
            git('config', 'user.name', 'Spack Build Bot')

            git('add', '.')

            tty.msg('git commit')
            commit_message = '{0} {1} ({2})'.format(
                'Auto-generated commit testing', branch_name, commit_sha)

            git('commit', '-m', '{0}'.format(commit_message))

            tty.msg('git push')
            git('remote', 'add', 'downstream', downstream_repo)
            push_to_branch = 'master:multi-ci-{0}'.format(branch_name)
            git('push', '--force', 'downstream', push_to_branch)

            shutil.rmtree('.git')
            shutil.move(saved_git_dir, '.git')
            git('reset', '--hard', 'HEAD')


def ci(parser, args):
    if args.func:
        args.func(args)
