# Copyright 2013-2018 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)


from spack import *


class RRbokeh(RPackage):
    """R interface for creating plots in Bokeh. Bokeh by Continuum
    Analytics."""

    homepage = "https://hafen.github.io/rbokeh"
    url      = "https://cran.r-project.org/src/contrib/rbokeh_0.5.0.tar.gz"
    list_url = "https://cran.r-project.org/src/contrib/Archive/rbokeh"

    version('0.5.0', '4e14778c3fbd9286460ca28c68f57d10')

    depends_on('r-htmlwidgets', type=('build', 'run'))
    depends_on('r-maps', type=('build', 'run'))
    depends_on('r-jsonlite', type=('build', 'run'))
    depends_on('r-digest', type=('build', 'run'))
    depends_on('r-hexbin', type=('build', 'run'))
    depends_on('r-lazyeval', type=('build', 'run'))
    depends_on('r-pryr', type=('build', 'run'))
    depends_on('r-magrittr', type=('build', 'run'))
    depends_on('r-ggplot2', type=('build', 'run'))
    depends_on('r-scales', type=('build', 'run'))
    depends_on('r-gistr', type=('build', 'run'))
