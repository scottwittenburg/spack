# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

"""
This file contains code for creating spack mirror directories.  A
mirror is an organized hierarchy containing specially named archive
files.  This enabled spack to know where to find files in a mirror if
the main server for a particular package is down.  Or, if the computer
where spack is run is not connected to the internet, it allows spack
to download packages directly from a mirror (e.g., on an intranet).
"""
import re
import sys
import os
import os.path

try:
    from urllib.parse import urlparse, ParseResult
except ImportError:
    from urlparse import urlparse, ParseResult

import llnl.util.tty as tty
from llnl.util.filesystem import mkdirp

import spack.config
import spack.error
import spack.url as url
import spack.fetch_strategy as fs
from spack.spec import Spec
from spack.version import VersionList
from spack.util.compression import allowed_archive


def mirror_archive_filename(spec, fetcher, resource_id=None):
    """Get the name of the spec's archive in the mirror."""
    if not spec.version.concrete:
        raise ValueError("mirror.path requires spec with concrete version.")

    if isinstance(fetcher, fs.URLFetchStrategy):
        if fetcher.expand_archive:
            # If we fetch with a URLFetchStrategy, use URL's archive type
            ext = url.determine_url_file_extension(fetcher.url)

            # If the filename does not end with a normal suffix,
            # see if the package explicitly declares the extension
            if not ext:
                ext = spec.package.versions[spec.package.version].get(
                    'extension', None)

            if ext:
                # Remove any leading dots
                ext = ext.lstrip('.')

            if not ext:
                msg = """\
Unable to parse extension from {0}.

If this URL is for a tarball but does not include the file extension
in the name, you can explicitly declare it with the following syntax:

    version('1.2.3', 'hash', extension='tar.gz')

If this URL is for a download like a .jar or .whl that does not need
to be expanded, or an uncompressed installation script, you can tell
Spack not to expand it with the following syntax:

    version('1.2.3', 'hash', expand=False)
"""
                raise MirrorError(msg.format(fetcher.url))
        else:
            # If the archive shouldn't be expanded, don't check extension.
            ext = None
    else:
        # Otherwise we'll make a .tar.gz ourselves
        ext = 'tar.gz'

    if resource_id:
        filename = "%s-%s" % (resource_id, spec.version) + ".%s" % ext
    else:
        filename = "%s-%s" % (spec.package.name, spec.version) + ".%s" % ext

    return filename


def mirror_archive_path(spec, fetcher, resource_id=None):
    """Get the relative path to the spec's archive within a mirror."""
    return os.path.join(
        spec.name, mirror_archive_filename(spec, fetcher, resource_id))


def get_matching_versions(specs, **kwargs):
    """Get a spec for EACH known version matching any spec in the list.
    For concrete specs, this retrieves the concrete version and, if more
    than one version per spec is requested, retrieves the latest versions
    of the package.
    """
    matching = []
    for spec in specs:
        pkg = spec.package

        # Skip any package that has no known versions.
        if not pkg.versions:
            tty.msg("No safe (checksummed) versions for package %s" % pkg.name)
            continue

        pkg_versions = kwargs.get('num_versions', 1)

        version_order = list(reversed(sorted(pkg.versions)))
        matching_spec = []
        if spec.concrete:
            matching_spec.append(spec)
            pkg_versions -= 1
            if spec.version in version_order:
                version_order.remove(spec.version)

        for v in version_order:
            # Generate no more than num_versions versions for each spec.
            if pkg_versions < 1:
                break

            # Generate only versions that satisfy the spec.
            if spec.concrete or v.satisfies(spec.versions):
                s = Spec(pkg.name)
                s.versions = VersionList([v])
                s.variants = spec.variants.copy()
                # This is needed to avoid hanging references during the
                # concretization phase
                s.variants.spec = s
                matching_spec.append(s)
                pkg_versions -= 1

        if not matching_spec:
            tty.warn("No known version matches spec: %s" % spec)
        matching.extend(matching_spec)

    return matching


def suggest_archive_basename(resource):
    """Return a tentative basename for an archive.

    Raises:
        RuntimeError: if the name is not an allowed archive type.
    """
    basename = os.path.basename(resource.fetcher.url)
    if not allowed_archive(basename):
        raise RuntimeError("%s is not an allowed archive tye" % basename)
    return basename


def create(path, specs, **kwargs):
    """Create a directory to be used as a spack mirror, and fill it with
    package archives.

    Arguments:
        path: Path to create a mirror directory hierarchy in.
        specs: Any package versions matching these specs will be added \
            to the mirror.

    Keyword args:
        num_versions: Max number of versions to fetch per spec, \
            (default is 1 each spec)

    Return Value:
        Returns a tuple of lists: (present, mirrored, error)

        * present:  Package specs that were already present.
        * mirrored: Package specs that were successfully mirrored.
        * error:    Package specs that failed to mirror due to some error.

    This routine iterates through all known package versions, and
    it creates specs for those versions.  If the version satisfies any spec
    in the specs list, it is downloaded and added to the mirror.
    """
    # Make sure nothing is in the way.
    if os.path.isfile(path):
        raise MirrorError("%s already exists and is a file." % path)

    # automatically spec-ify anything in the specs array.
    specs = [s if isinstance(s, Spec) else Spec(s) for s in specs]

    # Get concrete specs for each matching version of these specs.
    version_specs = get_matching_versions(
        specs, num_versions=kwargs.get('num_versions', 1))
    for s in version_specs:
        s.concretize()

    # Get the absolute path of the root before we start jumping around.
    mirror_root = os.path.abspath(path)
    if not os.path.isdir(mirror_root):
        try:
            mkdirp(mirror_root)
        except OSError as e:
            raise MirrorError(
                "Cannot create directory '%s':" % mirror_root, str(e))

    # Things to keep track of while parsing specs.
    categories = {
        'present': [],
        'mirrored': [],
        'error': []
    }

    mirror_cache = spack.caches.MirrorCache(mirror_root)
    try:
        spack.caches.mirror_cache = mirror_cache
        # Iterate through packages and download all safe tarballs for each
        for spec in version_specs:
            add_single_spec(spec, mirror_root, categories, **kwargs)
    finally:
        spack.caches.mirror_cache = None

    categories['mirrored'] = list(mirror_cache.new_resources)
    categories['present'] = list(mirror_cache.existing_resources)

    return categories['present'], categories['mirrored'], categories['error']


def add_single_spec(spec, mirror_root, categories, **kwargs):
    tty.msg("Adding package {pkg} to mirror".format(
        pkg=spec.format("{name}{@version}")
    ))
    try:
        spec.package.do_fetch()
        spec.package.do_clean()

    except Exception as e:
        tty.debug(e)
        if spack.config.get('config:debug'):
            sys.excepthook(*sys.exc_info())
        else:
            tty.warn(
                "Error while fetching %s" % spec.cformat('{name}{@version}'),
                e.message)
        categories['error'].append(spec)


def _missing_s3_bucket(path):
    return MirrorError(
            "Missing bucket name in S3 mirror URL path: '{0}'".format(path))

def _bad_url_scheme(scheme):
    return MirrorError(
            "Unrecognized or unsupported mirror URL scheme: '{0}'".format(
                scheme))

def mirror_url_parse(url):
    """Parse a mirror url."""
    (scheme, netloc, path, params, query, _) = urlparse(
            url, scheme='file', allow_fragments=False)

    scheme = scheme.lower()
    extra_attrs = {}

    if scheme == 'file':
        path = spack.util.path.canonicalize_path(path)
    elif scheme in ('http', 'https'):
        pass # no further processing needed
    elif scheme == 's3':
        if not os.path.isabs(path):
            raise _missing_s3_bucket(path)

        path_tokens = os.path.split(path)
        if len(path_tokens) < 2:
            raise _missing_s3_bucket(path)

        bucket_name = path_tokens[1]
        path = os.path.join('', *path_tokens[2:])

        extra_attrs["s3_bucket"] = bucket_name

        credentials, netloc = (netloc.split('@', 1) + [None])[:2]
        if netloc is None:
            netloc, credentials = credentials, netloc

        if credentials:
            key_id, key_secret = (credentials.split(':', 1) + [None])[:2]

            if key_secret is None:
                extra_attrs["s3_profile"] = key_id
            else:
                extra_attrs["s3_access_key_id"] = key_id
                extra_attrs["s3_secret_access_key"] = key_secret
    else:
        raise _bad_url_scheme(scheme)

    result = ParseResult(scheme=scheme,
                         netloc=netloc,
                         path=path,
                         params=params,
                         query=query,
                         fragment=None)

    for key, val in extra_attrs.items():
        setattr(result, key, val)

    return result


def mirror_url_format(parsed_url):
    if isinstance(parsed_url, basestring):
        parsed_url = mirror_url_parse(parsed_url)

    (scheme, netloc, path, params, query, _) = parsed_url

    scheme = scheme.lower()

    if scheme in ('file', 'http', 'https'):
        pass # no further processing needed
    elif scheme == 's3':
        path = os.path.join(
                *[x for x in ('/', parsed_url.s3_bucket, path) if x])

        credentials = None

        try:
            credentials = parsed_url.s3_profile
        except AttributeError:
            pass

        try:
            if not credentials:
                credentials = ':'.join((
                    parsed_url.s3_access_key_id,
                    parsed_url.s3_secret_access_key))
        except AttributeError:
            pass

        if credentials:
            netloc = '@'.join((credentials, netloc))
    else:
        raise _bad_url_scheme(scheme)

    # Workaround a quirk of urlparse where the double-slash after the [scheme]
    # is left out in the case of no [netloc] and a [scheme] that is not natively
    # recognized (e.g.: s3).  S3 URLs will usually specify no [netloc], instead
    # defaulting to AWS and using other mechanisms, like environment variables,
    # to provide access credentials.
    #
    # The point of this workaround is to manipulate these URLs so that instead
    # of being formatted like so:
    #
    #  s3:/my-bucket
    #
    # ... they would be (correctly) formatted like so:
    #
    #  s3:///my-bucket
    #
    if not netloc and scheme not in ('http', 'https', 'file'):
        if not path:
            path = '//'
        else:
            path_tokens = os.path.split(path)
            netloc = os.path.join(*path_tokens[:2])
            path = os.path.join('/', *path_tokens[2:])
            if path == '/':
                path = ''

    return ParseResult(scheme=scheme,
                       netloc=netloc,
                       path=path,
                       params=params,
                       query=query,
                       fragment=None).geturl()


class MirrorError(spack.error.SpackError):
    """Superclass of all mirror-creation related errors."""

    def __init__(self, msg, long_msg=None):
        super(MirrorError, self).__init__(msg, long_msg)
