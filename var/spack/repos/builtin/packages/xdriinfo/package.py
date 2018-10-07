# Copyright 2013-2018 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack import *


class Xdriinfo(AutotoolsPackage):
    """xdriinfo - query configuration information of X11 DRI drivers."""

    homepage = "http://cgit.freedesktop.org/xorg/app/xdriinfo"
    url      = "https://www.x.org/archive/individual/app/xdriinfo-1.0.5.tar.gz"

    version('1.0.5', '34a4a9ae69c60f4c2566bf9ea4bcf311')

    depends_on('libx11')
    depends_on('expat')
    depends_on('libxshmfence')
    depends_on('libxext')
    depends_on('libxdamage')
    depends_on('libxfixes')
    depends_on('pcre')

    depends_on('glproto', type='build')
    depends_on('pkgconfig', type='build')
    depends_on('util-macros', type='build')
