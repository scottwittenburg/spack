# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from __future__ import print_function

import argparse
import os

import llnl.util.tty as tty
import spack.build_environment as build_environment
import spack.cmd
import spack.cmd.common.arguments as arguments

description = "show install environment for a spec, and run commands"
section = "build"
level = "long"


def setup_parser(subparser):
    arguments.add_common_arguments(subparser, ['clean', 'dirty', 'specs'])


def build_env(parser, args):
    if not args.specs:
        tty.die("spack build-env requires a spec.")

    # Specs may have spaces in them, so if they do, require that the
    # caller put a '--' between the spec and the command to be
    # executed.  If there is no '--', assume that the spec is the
    # first argument.
    sep = '--'
    if sep in args.specs:
        s = args.specs.index(sep)
        spec = args.specs[:s]
        cmd = args.specs[s + 1:]
    else:
        spec = args.specs[0]
        cmd = args.specs[1:]

    specs = spack.cmd.parse_specs(spec, concretize=True)
    if len(specs) > 1:
        tty.die("spack build-env only takes one spec.")
    spec = specs[0]

    build_environment.setup_package(spec.package, args.dirty)

    if not cmd:
        # If no command act like the "env" command and print out env vars.
        for key, val in os.environ.items():
            print("%s=%s" % (key, val))

    else:
        # Otherwise execute the command with the new environment
        os.execvp(cmd[0], cmd)
