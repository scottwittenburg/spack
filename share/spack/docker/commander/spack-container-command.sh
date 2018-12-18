#!/bin/bash

# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

PATH_PREFIX="${1}"
shift
export PATH="${PATH_PREFIX}:${PATH}"

SPACK_COMMAND=""

for i in "${@}"
do
    SPACK_COMMAND="${SPACK_COMMAND} ${i}"
done

echo "Spack command: ${SPACK_COMMAND}"

SPACK_COMMAND_OUTPUT=$(${SPACK_COMMAND})

echo -e "<BEGIN_SPACK_COMMAND_OUTPUT>\n${SPACK_COMMAND_OUTPUT}\n<END_SPACK_COMMAND_OUTPUT>"
