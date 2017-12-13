##############################################################################
# Copyright (c) 2013-2017, Lawrence Livermore National Security, LLC.
# Produced at the Lawrence Livermore National Laboratory.
#
# This file is part of Spack.
# Created by Todd Gamblin, tgamblin@llnl.gov, All rights reserved.
# LLNL-CODE-647188
#
# For details, see https://github.com/spack/spack
# Please also see the NOTICE and LICENSE files for our notice and the LGPL.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License (as
# published by the Free Software Foundation) version 2.1, February 1999.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the IMPLIED WARRANTY OF
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the terms and
# conditions of the GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
##############################################################################

import os
from spack import *


class Vtk(CMakePackage):
    """The Visualization Toolkit (VTK) is an open-source, freely
    available software system for 3D computer graphics, image
    processing and visualization. """

    homepage = "http://www.vtk.org"
    url      = "http://www.vtk.org/files/release/8.0/VTK-8.0.1.tar.gz"
    list_url = "http://www.vtk.org/download/"

    version('8.0.1', '692d09ae8fadc97b59d35cab429b261a')
    version('7.1.0', 'a7e814c1db503d896af72458c2d0228f')
    version('7.0.0', '5fe35312db5fb2341139b8e4955c367d')
    version('6.3.0', '0231ca4840408e9dd60af48b314c5b6d')
    version('6.1.0', '25e4dfb3bad778722dcaec80cd5dab7d')

    # VTK7 defaults to OpenGL2 rendering backend
    variant('opengl2', default=True, description='Enable OpenGL2 backend')
    variant('osmesa', default=False, description='Enable OSMesa support')
    variant('python', default=False, description='Enable Python support')
    variant('qt', default=False, description='Build with support for Qt')

    patch('gcc.patch', when='@6.1.0')

    # If you didn't ask for osmesa, then hw rendering using vendor-specific
    # drivers is faster, but it must be done externally.
    depends_on('opengl', when='~osmesa')

    # mesa default is software rendering, make it faster with llvm
    depends_on('mesa+llvm', when='+osmesa')

    # VTK will need Qt5OpenGL, and qt needs '-opengl' for that
    depends_on('qt+opengl', when='+qt')

    depends_on('expat')
    depends_on('freetype')
    depends_on('glew')
    depends_on('hdf5')
    depends_on('libjpeg')
    depends_on('jsoncpp')
    depends_on('libharu')
    depends_on('libxml2')
    depends_on('lz4')
    depends_on('netcdf')
    depends_on('netcdf-cxx')
    depends_on('libpng')
    depends_on('libtiff')
    depends_on('zlib')

    extends('python', when='+python')

    def url_for_version(self, version):
        url = "http://www.vtk.org/files/release/{0}/VTK-{1}.tar.gz"
        return url.format(version.up_to(2), version)

    def setup_environment(self, spack_env, run_env):
        # VTK has some trouble finding freetype unless it is set in
        # the environment
        spack_env.set('FREETYPE_DIR', self.spec['freetype'].prefix)

    def cmake_args(self):
        spec = self.spec

        opengl_ver = 'OpenGL{0}'.format('2' if '+opengl2' in spec else '')

        cmake_args = std_cmake_args[:]
        cmake_args.extend([
            '-DBUILD_SHARED_LIBS=ON',
            '-DVTK_RENDERING_BACKEND:STRING={0}'.format(opengl_ver),

            '-DVTK_USE_SYSTEM_LIBRARIES=ON',

            '-DVTK_USE_SYSTEM_GL2PS=OFF',
            '-DVTK_USE_SYSTEM_LIBPROJ4=OFF',
            '-DVTK_USE_SYSTEM_OGGTHEORA=OFF',

            '-DNETCDF_DIR={0}'.format(spec['netcdf'].prefix),
            '-DNETCDF_C_ROOT={0}'.format(spec['netcdf'].prefix),
            '-DNETCDF_CXX_ROOT={0}'.format(spec['netcdf-cxx'].prefix),

            # Enable/Disable wrappers for Python.
            '-DVTK_WRAP_PYTHON={0}'.format(
                'ON' if '+python' in spec else 'OFF'),

            # Disable wrappers for other languages.
            '-DVTK_WRAP_JAVA=OFF',
            '-DVTK_WRAP_TCL=OFF',
        ])

        if '+qt' in spec:
            qt_ver = spec['qt'].version.up_to(1)
            qt_bin = spec['qt'].prefix.bin
            qmake_exe = os.path.join(qt_bin, 'qmake')

            cmake_args.extend([
                # Enable Qt support here.
                '-DVTK_QT_VERSION:STRING={0}'.format(qt_ver),
                '-DQT_QMAKE_EXECUTABLE:PATH={0}'.format(qmake_exe),
                '-DVTK_Group_Qt:BOOL=ON',
            ])

            # NOTE: The following definitions are required in order to allow
            # VTK to build with qt~webkit versions (see the documentation for
            # more info: http://www.vtk.org/Wiki/VTK/Tutorials/QtSetup).
            if '~webkit' in spec['qt']:
                cmake_args.extend([
                    '-DVTK_Group_Qt:BOOL=OFF',
                    '-DModule_vtkGUISupportQt:BOOL=ON',
                    '-DModule_vtkGUISupportQtOpenGL:BOOL=ON',
                ])

        if '+osmesa' in spec:
            prefix = spec['mesa'].prefix
            osmesaIncludeDir = prefix.include
            osmesaLibrary = os.path.join(prefix.lib, 'libOSMesa.so')
            cmake_args.extend([
                '-DVTK_USE_X:BOOL=OFF',
                '-DVTK_OPENGL_HAS_OSMESA:BOOL=ON',
                '-DOSMESA_INCLUDE_DIR:PATH={0}'.format(osmesaIncludeDir),
                '-DOSMESA_LIBRARY:FILEPATH={0}'.format(osmesaLibrary),
            ])
        else:
            prefix = spec['opengl'].prefix
            openglIncludeDir = prefix.include
            openglLibrary = os.path.join(prefix.lib, 'libGL.so')
            cmake_args.extend([
                '-DOPENGL_INCLUDE_DIR:PATH={0}'.format(openglIncludeDir),
                '-DOPENGL_gl_LIBRARY:FILEPATH={0}'.format(openglLibrary)
            ])

        if spec.satisfies('@:6.1.0'):
            cmake_args.append('-DCMAKE_C_FLAGS=-DGLX_GLXEXT_LEGACY')
            cmake_args.append('-DCMAKE_CXX_FLAGS=-DGLX_GLXEXT_LEGACY')

        return cmake_args
