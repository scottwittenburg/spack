# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import argparse
import spack.util.spack_yaml as syaml


def update_compiler(compilers_path, prefix, rpaths):
    with open(compilers_path, 'r') as fd:
        compilers_text = fd.read()

    compilers = syaml.load(compilers_text)

    for c in compilers['compilers']:
        if c['compiler']['paths']['cc'].startswith(prefix):
            print('found target compiler: {0}'.format(c['compiler']['spec']))
            c['compiler']['extra_rpaths'].append(rpaths)

    with open(compilers_path, 'w') as fd:
        fd.write(syaml.dump(compilers))


if __name__ == "__main__":
    # Create argument parser
    parser = argparse.ArgumentParser(
        description="Add extra_rpaths to compiler")

    parser.add_argument('-c', '--compilers-yaml-path',
                        default='/root/.spack/linux/compilers.yaml',
                        help="Absolute path to compilers.yaml to update")
    parser.add_argument('-p', '--prefix', default=None,
                        help="Install prefix of compiler to update")
    parser.add_argument('-r', '--rpaths', default=None,
                        help="Extra rpaths to add to target compiler")

    args = parser.parse_args()

    update_compiler(args.compilers_yaml_path, args.prefix, args.rpaths)
