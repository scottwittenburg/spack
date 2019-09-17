#! /usr/bin/env bash
#
# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

. "$(pwd)/share/spack/setup-env.sh"
spack -d ci generate
