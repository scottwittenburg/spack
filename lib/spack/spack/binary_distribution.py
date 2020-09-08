# Copyright 2013-2020 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import codecs
import os
import re
import tarfile
import shutil
import tempfile
import hashlib
import glob

from contextlib import closing
import ruamel.yaml as yaml

import json

from six.moves.urllib.error import URLError, HTTPError

import llnl.util.lang
import llnl.util.tty as tty
from llnl.util.filesystem import mkdirp

import spack.cmd
import spack.config as config
import spack.database as spack_db
import spack.fetch_strategy as fs
import spack.util.file_cache as file_cache
import spack.util.gpg
import spack.relocate as relocate
import spack.util.spack_yaml as syaml
import spack.mirror
import spack.util.url as url_util
import spack.util.web as web_util
from spack.spec import Spec
from spack.stage import Stage
from spack.util.gpg import Gpg


#: default root, relative to the Spack install path
default_manager_root = os.path.join(spack.paths.opt_path, 'spack')

_build_cache_relative_path = 'build_cache'


class BinaryDistributionCacheManager(object):
    """
    The BinaryDistributionCacheManager stores and manages both:

    1. cached buildcache index files (the index.json describing the built
    specs available on a mirror)

    2. a cache of all the conrcrete built specs available on all the
    configured mirrors.

    TODO:

        - Use spack.util.FileCache for cached index files and contents.json (use transactions)
        - implement the invalidate_built_spec_cache() method
        - in update_local_index_cache(), finish handling mirros not yet in our cache
        - decide when, aside from just on init, we will update the built_spec_cache from the cached indices
    """

    def __init__(self, cache_root=None):
        tty.debug('Constructing BinaryDistributionCacheManager')
        self._cache_root = default_manager_root
        if cache_root:
            self._cache_root = cache_root
        self._index_cache_root = os.path.join(self._cache_root, 'indices')
        self._index_contents_key = 'contents.json'
        self._index_file_cache = None
        self._local_index_cache = None
        self._built_spec_cache = {}

    def _init_local_index_cache(self):
        tty.debug('_init_local_index_cache')
        if not self._index_file_cache:
            tty.debug('_init_local_index_cache -> one time initialization')
            self._index_file_cache = file_cache.FileCache(self._index_cache_root)

            cache_key = self._index_contents_key
            self._index_file_cache.init_entry(cache_key)

            cache_path = self._index_file_cache.cache_path(cache_key)

            self._local_index_cache = {}
            if os.path.exists(cache_path) and os.path.isfile(cache_path):
                with self._index_file_cache.read_transaction(cache_key) as cache_file:
                    self._local_index_cache = json.load(cache_file)

    @property
    def spec_cache(self):
        """The cache of specs and mirrors they live on"""
        return self._built_spec_cache

    def _write_local_index_cache(self):
        tty.debug('_write_local_index_cache')
        self._init_local_index_cache()
        cache_key = self._index_contents_key
        with self._index_file_cache.write_transaction(cache_key) as (old, new):
            json.dump(self._local_index_cache, new)

    def regenerate_spec_cache(self):
        tty.debug('regenerate_spec_cache')

        self._init_local_index_cache()

        for mirror_url in self._local_index_cache:
            cache_entry = self._local_index_cache[mirror_url]
            cached_index_hash = cache_entry['index_hash']
            cached_index_path = cache_entry['index_path']

            tty.debug('Inserting built specs from cache key {0}'
                  .format(cached_index_path))

            self._associate_built_specs_with_mirror(cached_index_path,
                                                    mirror_url)

        self._built_spec_cache_invalid = False

    def _associate_built_specs_with_mirror(self, cache_key, mirror_url):
        self._init_local_index_cache()

        tmpdir = tempfile.mkdtemp()

        try:
            db_root_dir = os.path.join(tmpdir, 'db_root')
            db = spack_db.Database(None, db_dir=db_root_dir,
                                   enable_transaction_locking=False)

            self._index_file_cache.init_entry(cache_key)
            cache_path = self._index_file_cache.cache_path(cache_key)
            with self._index_file_cache.read_transaction(cache_key) as cache_file:
                db._read_from_file(cache_path)

            spec_list = db.query_local(installed=False)

            for indexed_spec in spec_list:
                indexed_spec_dag_hash = indexed_spec.dag_hash()
                if indexed_spec_dag_hash not in self._built_spec_cache:
                    self._built_spec_cache[indexed_spec_dag_hash] = []
                self._built_spec_cache[indexed_spec_dag_hash].append({
                    "mirror_url": mirror_url,
                    "spec": indexed_spec,
                })
        finally:
            shutil.rmtree(tmpdir)

    def get_all_built_specs(self):
        spec_list = []
        for dag_hash in self._built_spec_cache:
            # in the absence of further information, all concrete specs
            # with the same DAG hash are equivalent, so we can just
            # return the first one in the list.
            if len(self._built_spec_cache[dag_hash]) > 0:
                spec_list.append(self._built_spec_cache[dag_hash][0]['spec'])

        return spec_list

    def find_built_spec(self, spec):
        """Find the built spec corresponding to ``spec``.

        If the spec can be found among the confighured binary mirrors, an
        object is returned that contains the concrete spec and the mirror url
        where it can be found.  Otherwise, ``None`` is returned.

        Args:
            spec (Spec): Concrete spec to find

        Returns:
            An list of objects containing the found specs mirror url where
                each can be found:

                .. code-block:: JSON

                    [
                        {
                            "spec": <concrete-spec>,
                            "mirror_url": <mirror-root-url>
                        }, ...
                    ]
        """
        find_hash = spec.dag_hash()
        if find_hash not in self._built_spec_cache:
            return None

        return self._built_spec_cache[find_hash]


    def update_spec(self, spec, found_list):
        """
        Take list of {'mirror_url': m, 'spec': s} objects and update the local
        built_spec_cache
        """
        spec_dag_hash = spec.dag_hash()

        if spec_dag_hash not in self._built_spec_cache:
            self._built_spec_cache[spec_dag_hash] = found_list
        else:
            current_list = self._built_spec_cache[spec_dag_hash]
            for new_entry in found_list:
                for cur_entry in current_list:
                    if new_entry['mirror_url'] == cur_entry['mirror_url']:
                        cur_entry['spec'] = new_entry['spec']
                        break
                else:
                    current_list.append = {
                        'mirror_url': new_entry['mirror_url'],
                        'spec': new_entry['spec'],
                    }

    def update_local_index_cache(self):
        """ Make sure local cache of buildcache index files is up to date."""
        self._init_local_index_cache()

        mirrors = spack.mirror.MirrorCollection()
        configured_mirror_urls = [m.fetch_url for m in mirrors.values()]
        items_to_remove = []

        # First compare the mirror urls currently present in the cache to the
        # configured mirrors.  If we have a cached index for a mirror which is
        # no longer configured, we should remove it from the cache.  For any
        # cached indices corresponding to currently configured mirrors, we need
        # to check if the cache is still good, or needs to be updated.
        # Finally, if there are configured mirrors for which we don't have a
        # cache entry, we need to fetch and cache the indices from those
        # mirrors.

        for cached_mirror_url in self._local_index_cache:
            cache_entry = self._local_index_cache[cached_mirror_url]
            cached_index_hash = cache_entry['index_hash']
            cached_index_path = cache_entry['index_path']
            if cached_mirror_url in configured_mirror_urls:
                # May need to fetch the index and update the local caches
                self.fetch_and_cache_index(
                    cached_mirror_url, expect_hash=cached_index_hash)
            else:
                # No longer have this mirror, cached index should be removed
                items_to_remove.append({
                    'url': cached_mirror_url,
                    'cache_key': os.path.join(self._index_cache_root,
                                              cached_index_path)
                })

        # Clean up items to be removed, identified above
        for item in items_to_remove:
            url = item['url']
            cache_key = item['cache_key']
            self._index_file_cache.remove(cache_key)
            del self._local_index_cache[url]

        # Iterate the configured mirrors now.  Any mirror urls we do not
        # already have in our cache must be fetched, stored, and represented
        # locally.
        for mirror_url in configured_mirror_urls:
            if mirror_url not in self._local_index_cache:
                # Need to fetch the index and update the local caches
                self.fetch_and_cache_index(mirror_url)

        self._write_local_index_cache()

    def fetch_and_cache_index(self, mirror_url, expect_hash=None,
                              update_spec_cache=True):
        """ Fetch a buildcache index file from a remote mirror and cache it.

        If we already have a cached index from this mirror, then we first
        check if the hash has changed, and we avoid fetching it if not.

        Args:
            mirror_url (str): Base url of mirror
            expect_hash (str): If provided, this hash will be compared against
                the index hash we retrieve from the mirror, to determine if we
                need to fetch the index or not.
            update_spec_cache (bool): Whether or not to update the cache of
                specs if we needed to fetch the index.
        """
        index_fetch_url = url_util.join(
            mirror_url, _build_cache_relative_path, 'index.json')
        hash_fetch_url = url_util.join(
            mirror_url, _build_cache_relative_path, 'index.json.hash')

        fetched_hash = ''

        # Fetch the hash first so we can check if we actually need to fetch
        # the index itself.
        try:
            _, _, fs = web_util.read_from_url(
                hash_fetch_url, 'text/plain')
            fetched_hash = codecs.getreader('utf-8')(fs).read()
        except (URLError, web_util.SpackWebError) as url_err:
            tty.debug('Unable to read index hash {0}'.format(hash_fetch_url), url_err, 1)

        # IF we were expecting some hash and found that we got it, we're done
        if expect_hash and fetched_hash == expect_hash:
            tty.debug('Cached index for {0} already up to date'.format(
                mirror_url))
            return

        tty.debug('Fetching index from {0}'.format(index_fetch_url))

        # Fetch index itself
        try:
            _, _, fs = web_util.read_from_url(
                index_fetch_url, 'application/json')
            index_object_str = codecs.getreader('utf-8')(fs).read()
        except (URLError, web_util.SpackWebError) as url_err:
            tty.debug('Unable to read index {0}'.format(index_fetch_url), url_err, 1)
            return

        locally_computed_hash = compute_hash(index_object_str)

        if locally_computed_hash != fetched_hash:
            msg_tmpl = ('Computed hash ({0}) did not match remote ({1}), '
                        'indicating error in index transmission')
            tty.error(msg_tmpl.format(locally_computed_hash, expect_hash))
            return

        cache_key = 'index_{0}.json'.format(locally_computed_hash[:10])
        self._index_file_cache.init_entry(cache_key)
        with self._index_file_cache.write_transaction(cache_key) as (old, new):
            new.write(index_object_str)

        self._local_index_cache[mirror_url] = {
            'index_hash': locally_computed_hash,
            'index_path': cache_key,
        }

        self._built_spec_cache_invalid = True


def _cache_manager():
    """Get the singleton store instance."""
    cache_root = spack.config.get(
        'config:binary_distribution_cache_root', default_manager_root)
    cache_root = spack.util.path.canonicalize_path(cache_root)

    return BinaryDistributionCacheManager(cache_root)


#: Singleton cache_manager instance
cache_manager = llnl.util.lang.Singleton(_cache_manager)


class NoOverwriteException(spack.error.SpackError):
    """
    Raised when a file exists and must be overwritten.
    """

    def __init__(self, file_path):
        err_msg = "\n%s\nexists\n" % file_path
        err_msg += "Use -f option to overwrite."
        super(NoOverwriteException, self).__init__(err_msg)


class NoGpgException(spack.error.SpackError):
    """
    Raised when gpg2 is not in PATH
    """

    def __init__(self, msg):
        super(NoGpgException, self).__init__(msg)


class NoKeyException(spack.error.SpackError):
    """
    Raised when gpg has no default key added.
    """

    def __init__(self, msg):
        super(NoKeyException, self).__init__(msg)


class PickKeyException(spack.error.SpackError):
    """
    Raised when multiple keys can be used to sign.
    """

    def __init__(self, keys):
        err_msg = "Multiple keys available for signing\n%s\n" % keys
        err_msg += "Use spack buildcache create -k <key hash> to pick a key."
        super(PickKeyException, self).__init__(err_msg)


class NoVerifyException(spack.error.SpackError):
    """
    Raised if file fails signature verification.
    """
    pass


class NoChecksumException(spack.error.SpackError):
    """
    Raised if file fails checksum verification.
    """
    pass


class NewLayoutException(spack.error.SpackError):
    """
    Raised if directory layout is different from buildcache.
    """

    def __init__(self, msg):
        super(NewLayoutException, self).__init__(msg)


def compute_hash(data):
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def build_cache_relative_path():
    return _build_cache_relative_path


def build_cache_prefix(prefix):
    return os.path.join(prefix, build_cache_relative_path())


def buildinfo_file_name(prefix):
    """
    Filename of the binary package meta-data file
    """
    name = os.path.join(prefix, ".spack/binary_distribution")
    return name


def read_buildinfo_file(prefix):
    """
    Read buildinfo file
    """
    filename = buildinfo_file_name(prefix)
    with open(filename, 'r') as inputfile:
        content = inputfile.read()
        buildinfo = yaml.load(content)
    return buildinfo


def write_buildinfo_file(spec, workdir, rel=False):
    """
    Create a cache file containing information
    required for the relocation
    """
    prefix = spec.prefix
    text_to_relocate = []
    binary_to_relocate = []
    link_to_relocate = []
    blacklist = (".spack", "man")
    prefix_to_hash = dict()
    prefix_to_hash[str(spec.package.prefix)] = spec.dag_hash()
    deps = spack.build_environment.get_rpath_deps(spec.package)
    for d in deps:
        prefix_to_hash[str(d.prefix)] = d.dag_hash()
    # Do this at during tarball creation to save time when tarball unpacked.
    # Used by make_package_relative to determine binaries to change.
    for root, dirs, files in os.walk(prefix, topdown=True):
        dirs[:] = [d for d in dirs if d not in blacklist]
        for filename in files:
            path_name = os.path.join(root, filename)
            m_type, m_subtype = relocate.mime_type(path_name)
            if os.path.islink(path_name):
                link = os.readlink(path_name)
                if os.path.isabs(link):
                    # Relocate absolute links into the spack tree
                    if link.startswith(spack.store.layout.root):
                        rel_path_name = os.path.relpath(path_name, prefix)
                        link_to_relocate.append(rel_path_name)
                    else:
                        msg = 'Absolute link %s to %s ' % (path_name, link)
                        msg += 'outside of prefix %s ' % prefix
                        msg += 'should not be relocated.'
                        tty.warn(msg)

            if relocate.needs_binary_relocation(m_type, m_subtype):
                if not filename.endswith('.o'):
                    rel_path_name = os.path.relpath(path_name, prefix)
                    binary_to_relocate.append(rel_path_name)
            if relocate.needs_text_relocation(m_type, m_subtype):
                rel_path_name = os.path.relpath(path_name, prefix)
                text_to_relocate.append(rel_path_name)

    # Create buildinfo data and write it to disk
    buildinfo = {}
    buildinfo['relative_rpaths'] = rel
    buildinfo['buildpath'] = spack.store.layout.root
    buildinfo['spackprefix'] = spack.paths.prefix
    buildinfo['relative_prefix'] = os.path.relpath(
        prefix, spack.store.layout.root)
    buildinfo['relocate_textfiles'] = text_to_relocate
    buildinfo['relocate_binaries'] = binary_to_relocate
    buildinfo['relocate_links'] = link_to_relocate
    buildinfo['prefix_to_hash'] = prefix_to_hash
    filename = buildinfo_file_name(workdir)
    with open(filename, 'w') as outfile:
        outfile.write(syaml.dump(buildinfo, default_flow_style=True))


def tarball_directory_name(spec):
    """
    Return name of the tarball directory according to the convention
    <os>-<architecture>/<compiler>/<package>-<version>/
    """
    return "%s/%s/%s-%s" % (spec.architecture,
                            str(spec.compiler).replace("@", "-"),
                            spec.name, spec.version)


def tarball_name(spec, ext):
    """
    Return the name of the tarfile according to the convention
    <os>-<architecture>-<package>-<dag_hash><ext>
    """
    return "%s-%s-%s-%s-%s%s" % (spec.architecture,
                                 str(spec.compiler).replace("@", "-"),
                                 spec.name,
                                 spec.version,
                                 spec.dag_hash(),
                                 ext)


def tarball_path_name(spec, ext):
    """
    Return the full path+name for a given spec according to the convention
    <tarball_directory_name>/<tarball_name>
    """
    return os.path.join(tarball_directory_name(spec),
                        tarball_name(spec, ext))


def checksum_tarball(file):
    # calculate sha256 hash of tar file
    block_size = 65536
    hasher = hashlib.sha256()
    with open(file, 'rb') as tfile:
        buf = tfile.read(block_size)
        while len(buf) > 0:
            hasher.update(buf)
            buf = tfile.read(block_size)
    return hasher.hexdigest()


def sign_tarball(key, force, specfile_path):
    # Sign the packages if keys available
    if spack.util.gpg.Gpg.gpg() is None:
        raise NoGpgException(
            "gpg2 is not available in $PATH .\n"
            "Use spack install gnupg and spack load gnupg.")

    if key is None:
        keys = Gpg.signing_keys()
        if len(keys) == 1:
            key = keys[0]

        if len(keys) > 1:
            raise PickKeyException(str(keys))

        if len(keys) == 0:
            msg = "No default key available for signing.\n"
            msg += "Use spack gpg init and spack gpg create"
            msg += " to create a default key."
            raise NoKeyException(msg)

    if os.path.exists('%s.asc' % specfile_path):
        if force:
            os.remove('%s.asc' % specfile_path)
        else:
            raise NoOverwriteException('%s.asc' % specfile_path)

    Gpg.sign(key, specfile_path, '%s.asc' % specfile_path)


def generate_package_index(cache_prefix):
    """Create the build cache index page.

    Creates (or replaces) the "index.json" page at the location given in
    cache_prefix.  This page contains a link for each binary package (*.yaml)
    and public key (*.key) under cache_prefix.
    """
    # TODO: cnnsider that if cache_prefix points to mirror url we have locally
    # TODO: configured, we could create a cache entry for the tndex we generate
    # TODO: here, and save the need to fetch/cache it later.
    tmpdir = tempfile.mkdtemp()
    db_root_dir = os.path.join(tmpdir, 'db_root')
    db = spack_db.Database(None, db_dir=db_root_dir,
                           enable_transaction_locking=False,
                           record_fields=['spec', 'ref_count'])

    file_list = (
        entry
        for entry in web_util.list_url(cache_prefix)
        if entry.endswith('.yaml'))

    tty.debug('Retrieving spec.yaml files from {0} to build index'.format(
        cache_prefix))
    for file_path in file_list:
        try:
            yaml_url = url_util.join(cache_prefix, file_path)
            tty.debug('fetching {0}'.format(yaml_url))
            _, _, yaml_file = web_util.read_from_url(yaml_url)
            yaml_contents = codecs.getreader('utf-8')(yaml_file).read()
            # yaml_obj = syaml.load(yaml_contents)
            # s = Spec.from_yaml(yaml_obj)
            s = Spec.from_yaml(yaml_contents)
            db.add(s, None)
        except (URLError, web_util.SpackWebError) as url_err:
            tty.error('Error reading spec.yaml: {0}'.format(file_path))
            tty.error(url_err)

    try:
        index_json_path = os.path.join(db_root_dir, 'index.json')
        with open(index_json_path, 'w') as f:
            db._write_to_file(f)

        # Read the index back in and compute it's hash
        with open(index_json_path) as f:
            index_string = f.read()
            index_hash = compute_hash(index_string)

        # Write the hash out to a local file
        index_hash_path = os.path.join(db_root_dir, 'index.json.hash')
        with open(index_hash_path, 'w') as f:
            f.write(index_hash)

        # Push the index itself
        web_util.push_to_url(
            index_json_path,
            url_util.join(cache_prefix, 'index.json'),
            keep_original=False,
            extra_args={'ContentType': 'application/json'})

        # Push the hash
        web_util.push_to_url(
            index_hash_path,
            url_util.join(cache_prefix, 'index.json.hash'),
            keep_original=False,
            extra_args={'ContentType': 'text/plain'})
    finally:
        shutil.rmtree(tmpdir)


def build_tarball(spec, outdir, force=False, rel=False, unsigned=False,
                  allow_root=False, key=None, regenerate_index=False):
    """
    Build a tarball from given spec and put it into the directory structure
    used at the mirror (following <tarball_directory_name>).
    """
    if not spec.concrete:
        raise ValueError('spec must be concrete to build tarball')

    # set up some paths
    tmpdir = tempfile.mkdtemp()
    cache_prefix = build_cache_prefix(tmpdir)

    tarfile_name = tarball_name(spec, '.tar.gz')
    tarfile_dir = os.path.join(cache_prefix, tarball_directory_name(spec))
    tarfile_path = os.path.join(tarfile_dir, tarfile_name)
    spackfile_path = os.path.join(
        cache_prefix, tarball_path_name(spec, '.spack'))

    remote_spackfile_path = url_util.join(
        outdir, os.path.relpath(spackfile_path, tmpdir))

    mkdirp(tarfile_dir)
    if web_util.url_exists(remote_spackfile_path):
        if force:
            web_util.remove_url(remote_spackfile_path)
        else:
            raise NoOverwriteException(url_util.format(remote_spackfile_path))

    # need to copy the spec file so the build cache can be downloaded
    # without concretizing with the current spack packages
    # and preferences
    spec_file = os.path.join(spec.prefix, ".spack", "spec.yaml")
    specfile_name = tarball_name(spec, '.spec.yaml')
    specfile_path = os.path.realpath(
        os.path.join(cache_prefix, specfile_name))

    remote_specfile_path = url_util.join(
        outdir, os.path.relpath(specfile_path, os.path.realpath(tmpdir)))

    if web_util.url_exists(remote_specfile_path):
        if force:
            web_util.remove_url(remote_specfile_path)
        else:
            raise NoOverwriteException(url_util.format(remote_specfile_path))

    # make a copy of the install directory to work with
    workdir = os.path.join(tmpdir, os.path.basename(spec.prefix))
    # install_tree copies hardlinks
    # create a temporary tarfile from prefix and exract it to workdir
    # tarfile preserves hardlinks
    temp_tarfile_name = tarball_name(spec, '.tar')
    temp_tarfile_path = os.path.join(tarfile_dir, temp_tarfile_name)
    with closing(tarfile.open(temp_tarfile_path, 'w')) as tar:
        tar.add(name='%s' % spec.prefix,
                arcname='.')
    with closing(tarfile.open(temp_tarfile_path, 'r')) as tar:
        tar.extractall(workdir)
    os.remove(temp_tarfile_path)

    # create info for later relocation and create tar
    write_buildinfo_file(spec, workdir, rel)

    # optionally make the paths in the binaries relative to each other
    # in the spack install tree before creating tarball
    if rel:
        try:
            make_package_relative(workdir, spec, allow_root)
        except Exception as e:
            shutil.rmtree(workdir)
            shutil.rmtree(tarfile_dir)
            shutil.rmtree(tmpdir)
            tty.die(e)
    else:
        try:
            check_package_relocatable(workdir, spec, allow_root)
        except Exception as e:
            shutil.rmtree(workdir)
            shutil.rmtree(tarfile_dir)
            shutil.rmtree(tmpdir)
            tty.die(e)

    # create gzip compressed tarball of the install prefix
    with closing(tarfile.open(tarfile_path, 'w:gz')) as tar:
        tar.add(name='%s' % workdir,
                arcname='%s' % os.path.basename(spec.prefix))
    # remove copy of install directory
    shutil.rmtree(workdir)

    # get the sha256 checksum of the tarball
    checksum = checksum_tarball(tarfile_path)

    # add sha256 checksum to spec.yaml
    with open(spec_file, 'r') as inputfile:
        content = inputfile.read()
        spec_dict = yaml.load(content)
    bchecksum = {}
    bchecksum['hash_algorithm'] = 'sha256'
    bchecksum['hash'] = checksum
    spec_dict['binary_cache_checksum'] = bchecksum
    # Add original install prefix relative to layout root to spec.yaml.
    # This will be used to determine is the directory layout has changed.
    buildinfo = {}
    buildinfo['relative_prefix'] = os.path.relpath(
        spec.prefix, spack.store.layout.root)
    buildinfo['relative_rpaths'] = rel
    spec_dict['buildinfo'] = buildinfo
    spec_dict['full_hash'] = spec.full_hash()

    tty.debug('The full_hash ({0}) of {1} will be written into {2}'.format(
        spec_dict['full_hash'],
        spec.name,
        url_util.format(remote_specfile_path)))
    tty.debug(spec.tree())

    with open(specfile_path, 'w') as outfile:
        outfile.write(syaml.dump(spec_dict))

    # sign the tarball and spec file with gpg
    if not unsigned:
        sign_tarball(key, force, specfile_path)
    # put tarball, spec and signature files in .spack archive
    with closing(tarfile.open(spackfile_path, 'w')) as tar:
        tar.add(name=tarfile_path, arcname='%s' % tarfile_name)
        tar.add(name=specfile_path, arcname='%s' % specfile_name)
        if not unsigned:
            tar.add(name='%s.asc' % specfile_path,
                    arcname='%s.asc' % specfile_name)

    # cleanup file moved to archive
    os.remove(tarfile_path)
    if not unsigned:
        os.remove('%s.asc' % specfile_path)

    web_util.push_to_url(
        spackfile_path, remote_spackfile_path, keep_original=False)
    web_util.push_to_url(
        specfile_path, remote_specfile_path, keep_original=False)

    tty.debug('Buildcache for "{0}" written to \n {1}'
              .format(spec, remote_spackfile_path))

    try:
        # create an index.html for the build_cache directory so specs can be
        # found
        if regenerate_index:
            generate_package_index(url_util.join(
                outdir, os.path.relpath(cache_prefix, tmpdir)))
    finally:
        shutil.rmtree(tmpdir)

    return None


def download_tarball(spec, url=None):
    """
    Download binary tarball for given package into stage area
    Return True if successful

    Args:
        spec (Spec): Concrete spec
        url (str): If provided, this is the preferred url, other configured
            mirrors will only be used if the tarball can't be retrieved from
            this preferred url.

    Returns:
        Path to the downloaded tarball, or ``None`` if the tarball could not
            be downloaded from any configured mirrors.
    """
    if not spack.mirror.MirrorCollection():
        tty.die("Please add a spack mirror to allow " +
                "download of pre-compiled packages.")

    tarball = tarball_path_name(spec, '.spack')

    urls_to_try = []

    if url:
        urls_to_try.append(url)

    for mirror in spack.mirror.MirrorCollection().values():
        if not url or url != mirror.fetch_url:
            urls_to_try.append(url_util.join(
                mirror.fetch_url, _build_cache_relative_path, tarball))

    for try_url in urls_to_try:
        # stage the tarball into standard place
        stage = Stage(url, name="build_cache", keep=True)
        stage.create()
        try:
            stage.fetch()
            return stage.save_filename
        except fs.FetchError:
            continue

    # If we arrive here, something went wrong, maybe this should be an
    # exception?
    return None


def make_package_relative(workdir, spec, allow_root):
    """
    Change paths in binaries to relative paths. Change absolute symlinks
    to relative symlinks.
    """
    prefix = spec.prefix
    buildinfo = read_buildinfo_file(workdir)
    old_layout_root = buildinfo['buildpath']
    orig_path_names = list()
    cur_path_names = list()
    for filename in buildinfo['relocate_binaries']:
        orig_path_names.append(os.path.join(prefix, filename))
        cur_path_names.append(os.path.join(workdir, filename))

    platform = spack.architecture.get_platform(spec.platform)
    if 'macho' in platform.binary_formats:
        relocate.make_macho_binaries_relative(
            cur_path_names, orig_path_names, old_layout_root)

    if 'elf' in platform.binary_formats:
        relocate.make_elf_binaries_relative(
            cur_path_names, orig_path_names, old_layout_root)

    relocate.raise_if_not_relocatable(cur_path_names, allow_root)
    orig_path_names = list()
    cur_path_names = list()
    for linkname in buildinfo.get('relocate_links', []):
        orig_path_names.append(os.path.join(prefix, linkname))
        cur_path_names.append(os.path.join(workdir, linkname))
    relocate.make_link_relative(cur_path_names, orig_path_names)


def check_package_relocatable(workdir, spec, allow_root):
    """
    Check if package binaries are relocatable.
    Change links to placeholder links.
    """
    buildinfo = read_buildinfo_file(workdir)
    cur_path_names = list()
    for filename in buildinfo['relocate_binaries']:
        cur_path_names.append(os.path.join(workdir, filename))
    relocate.raise_if_not_relocatable(cur_path_names, allow_root)


def relocate_package(spec, allow_root):
    """
    Relocate the given package
    """
    workdir = str(spec.prefix)
    buildinfo = read_buildinfo_file(workdir)
    new_layout_root = str(spack.store.layout.root)
    new_prefix = str(spec.prefix)
    new_rel_prefix = str(os.path.relpath(new_prefix, new_layout_root))
    new_spack_prefix = str(spack.paths.prefix)
    old_layout_root = str(buildinfo['buildpath'])
    old_spack_prefix = str(buildinfo.get('spackprefix'))
    old_rel_prefix = buildinfo.get('relative_prefix')
    old_prefix = os.path.join(old_layout_root, old_rel_prefix)
    rel = buildinfo.get('relative_rpaths')
    prefix_to_hash = buildinfo.get('prefix_to_hash', None)
    if (old_rel_prefix != new_rel_prefix and not prefix_to_hash):
        msg = "Package tarball was created from an install "
        msg += "prefix with a different directory layout and an older "
        msg += "buildcache create implementation. It cannot be relocated."
        raise NewLayoutException(msg)
    # older buildcaches do not have the prefix_to_hash dictionary
    # need to set an empty dictionary and add one entry to
    # prefix_to_prefix to reproduce the old behavior
    if not prefix_to_hash:
        prefix_to_hash = dict()
    hash_to_prefix = dict()
    hash_to_prefix[spec.format('{hash}')] = str(spec.package.prefix)
    new_deps = spack.build_environment.get_rpath_deps(spec.package)
    for d in new_deps:
        hash_to_prefix[d.format('{hash}')] = str(d.prefix)
    prefix_to_prefix = dict()
    for orig_prefix, hash in prefix_to_hash.items():
        prefix_to_prefix[orig_prefix] = hash_to_prefix.get(hash, None)
    prefix_to_prefix[old_prefix] = new_prefix
    prefix_to_prefix[old_layout_root] = new_layout_root

    tty.debug("Relocating package from",
              "%s to %s." % (old_layout_root, new_layout_root))

    def is_backup_file(file):
        return file.endswith('~')

    # Text files containing the prefix text
    text_names = list()
    for filename in buildinfo['relocate_textfiles']:
        text_name = os.path.join(workdir, filename)
        # Don't add backup files generated by filter_file during install step.
        if not is_backup_file(text_name):
            text_names.append(text_name)

# If we are not installing back to the same install tree do the relocation
    if old_layout_root != new_layout_root:
        files_to_relocate = [os.path.join(workdir, filename)
                             for filename in buildinfo.get('relocate_binaries')
                             ]
        # If the buildcache was not created with relativized rpaths
        # do the relocation of path in binaries
        platform = spack.architecture.get_platform(spec.platform)
        if 'macho' in platform.binary_formats:
            relocate.relocate_macho_binaries(files_to_relocate,
                                             old_layout_root,
                                             new_layout_root,
                                             prefix_to_prefix, rel,
                                             old_prefix,
                                             new_prefix)

        if 'elf' in platform.binary_formats:
            relocate.relocate_elf_binaries(files_to_relocate,
                                           old_layout_root,
                                           new_layout_root,
                                           prefix_to_prefix, rel,
                                           old_prefix,
                                           new_prefix)
            # Relocate links to the new install prefix
            links = [link for link in buildinfo.get('relocate_links', [])]
            relocate.relocate_links(
                links, old_layout_root, old_prefix, new_prefix
            )

        # For all buildcaches
        # relocate the install prefixes in text files including dependencies
        relocate.relocate_text(text_names,
                               old_layout_root, new_layout_root,
                               old_prefix, new_prefix,
                               old_spack_prefix,
                               new_spack_prefix,
                               prefix_to_prefix)

        paths_to_relocate = [old_prefix, old_layout_root]
        paths_to_relocate.extend(prefix_to_hash.keys())
        files_to_relocate = list(filter(
            lambda pathname: not relocate.file_is_relocatable(
                pathname, paths_to_relocate=paths_to_relocate),
            map(lambda filename: os.path.join(workdir, filename),
                buildinfo['relocate_binaries'])))
        # relocate the install prefixes in binary files including dependencies
        relocate.relocate_text_bin(files_to_relocate,
                                   old_prefix, new_prefix,
                                   old_spack_prefix,
                                   new_spack_prefix,
                                   prefix_to_prefix)

# If we are installing back to the same location
# relocate the sbang location if the spack directory changed
    else:
        if old_spack_prefix != new_spack_prefix:
            relocate.relocate_text(text_names,
                                   old_layout_root, new_layout_root,
                                   old_prefix, new_prefix,
                                   old_spack_prefix,
                                   new_spack_prefix,
                                   prefix_to_prefix)


def extract_tarball(spec, filename, allow_root=False, unsigned=False,
                    force=False):
    """
    extract binary tarball for given package into install area
    """
    if os.path.exists(spec.prefix):
        if force:
            shutil.rmtree(spec.prefix)
        else:
            raise NoOverwriteException(str(spec.prefix))

    tmpdir = tempfile.mkdtemp()
    stagepath = os.path.dirname(filename)
    spackfile_name = tarball_name(spec, '.spack')
    spackfile_path = os.path.join(stagepath, spackfile_name)
    tarfile_name = tarball_name(spec, '.tar.gz')
    tarfile_path = os.path.join(tmpdir, tarfile_name)
    specfile_name = tarball_name(spec, '.spec.yaml')
    specfile_path = os.path.join(tmpdir, specfile_name)

    with closing(tarfile.open(spackfile_path, 'r')) as tar:
        tar.extractall(tmpdir)
    # some buildcache tarfiles use bzip2 compression
    if not os.path.exists(tarfile_path):
        tarfile_name = tarball_name(spec, '.tar.bz2')
        tarfile_path = os.path.join(tmpdir, tarfile_name)
    if not unsigned:
        if os.path.exists('%s.asc' % specfile_path):
            try:
                suppress = config.get('config:suppress_gpg_warnings', False)
                Gpg.verify('%s.asc' % specfile_path, specfile_path, suppress)
            except Exception as e:
                shutil.rmtree(tmpdir)
                raise e
        else:
            shutil.rmtree(tmpdir)
            raise NoVerifyException(
                "Package spec file failed signature verification.\n"
                "Use spack buildcache keys to download "
                "and install a key for verification from the mirror.")
    # get the sha256 checksum of the tarball
    checksum = checksum_tarball(tarfile_path)

    # get the sha256 checksum recorded at creation
    spec_dict = {}
    with open(specfile_path, 'r') as inputfile:
        content = inputfile.read()
        spec_dict = syaml.load(content)
    bchecksum = spec_dict['binary_cache_checksum']

    # if the checksums don't match don't install
    if bchecksum['hash'] != checksum:
        shutil.rmtree(tmpdir)
        raise NoChecksumException(
            "Package tarball failed checksum verification.\n"
            "It cannot be installed.")

    new_relative_prefix = str(os.path.relpath(spec.prefix,
                                              spack.store.layout.root))
    # if the original relative prefix is in the spec file use it
    buildinfo = spec_dict.get('buildinfo', {})
    old_relative_prefix = buildinfo.get('relative_prefix', new_relative_prefix)
    rel = buildinfo.get('relative_rpaths')
    # if the original relative prefix and new relative prefix differ the
    # directory layout has changed and the  buildcache cannot be installed
    # if it was created with relative rpaths
    info = 'old relative prefix %s\nnew relative prefix %s\nrelative rpaths %s'
    tty.debug(info %
              (old_relative_prefix, new_relative_prefix, rel))
#    if (old_relative_prefix != new_relative_prefix and (rel)):
#        shutil.rmtree(tmpdir)
#        msg = "Package tarball was created from an install "
#        msg += "prefix with a different directory layout. "
#        msg += "It cannot be relocated because it "
#        msg += "uses relative rpaths."
#        raise NewLayoutException(msg)

    # extract the tarball in a temp directory
    with closing(tarfile.open(tarfile_path, 'r')) as tar:
        tar.extractall(path=tmpdir)
    # get the parent directory of the file .spack/binary_distribution
    # this should the directory unpacked from the tarball whose
    # name is unknown because the prefix naming is unknown
    bindist_file = glob.glob('%s/*/.spack/binary_distribution' % tmpdir)[0]
    workdir = re.sub('/.spack/binary_distribution$', '', bindist_file)
    tty.debug('workdir %s' % workdir)
    # install_tree copies hardlinks
    # create a temporary tarfile from prefix and exract it to workdir
    # tarfile preserves hardlinks
    temp_tarfile_name = tarball_name(spec, '.tar')
    temp_tarfile_path = os.path.join(tmpdir, temp_tarfile_name)
    with closing(tarfile.open(temp_tarfile_path, 'w')) as tar:
        tar.add(name='%s' % workdir,
                arcname='.')
    with closing(tarfile.open(temp_tarfile_path, 'r')) as tar:
        tar.extractall(spec.prefix)
    os.remove(temp_tarfile_path)

    # cleanup
    os.remove(tarfile_path)
    os.remove(specfile_path)

    try:
        relocate_package(spec, allow_root)
    except Exception as e:
        shutil.rmtree(spec.prefix)
        raise e
    else:
        manifest_file = os.path.join(spec.prefix,
                                     spack.store.layout.metadata_dir,
                                     spack.store.layout.manifest_file_name)
        if not os.path.exists(manifest_file):
            spec_id = spec.format('{name}/{hash:7}')
            tty.warn('No manifest file in tarball for spec %s' % spec_id)
    finally:
        shutil.rmtree(tmpdir)
        if os.path.exists(filename):
            os.remove(filename)


# Internal cache for downloaded specs
_cached_specs = set()


def try_direct_fetch(spec, force=False, full_hash_match=False):
    """
    Try to find the spec directly on the configured mirrors
    """
    specfile_name = tarball_name(spec, '.spec.yaml')
    lenient = not full_hash_match
    found_specs = []

    for mirror in spack.mirror.MirrorCollection().values():
        buildcache_fetch_url = url_util.join(
            mirror.fetch_url, _build_cache_relative_path, specfile_name)

        try:
            _, _, fs = web_util.read_from_url(
                buildcache_fetch_url, 'text/plain')
            fetched_spec_yaml = codecs.getreader('utf-8')(fs).read()
        except (URLError, web_util.SpackWebError, HTTPError) as url_err:
            tty.debug('Did not find {0} on {1}'.format(
                specfile_name, buildcache_fetch_url), url_err)
            continue

        # read the spec from the build cache file. All specs in build caches
        # are concrete (as they are built) so we need to mark this spec
        # concrete on read-in.
        fetched_spec = Spec.from_yaml(fetched_spec_yaml)
        fetched_spec._mark_concrete()

        if lenient or fetched_spec.full_hash() == spec.full_hash():
            found_specs.append({
                'mirror_url': mirror.fetch_url,
                'spec': fetched_spec,
            })

    return found_specs


def get_spec(spec=None, force=False, full_hash_match=False):
    """
    Check if concrete spec exists on mirrors and return an object
    indicating the mirrors on which it can be found
    """
    if spec is None:
        return []

    if not spack.mirror.MirrorCollection():
        tty.debug("No Spack mirrors are currently configured")
        return {}

    results = []
    lenient = not full_hash_match

    def filter_candidates(possibles):
        filtered_candidates = []
        for candidate in possibles:
            if lenient or spec.full_hash() == candidate['spec'].full_hash():
                filtered_candidates.append(candidate)

    candidates = cache_manager.find_built_spec(spec)
    if candidates:
        results = filter_candidates(candidates)

    if not results:
        results = try_direct_fetch(spec,
                                   force=force,
                                   full_hash_match=full_hash_match)
        if results:
            cache_manager.update_spec(spec, results)

    return results


def get_specs():
    """
    Get concrete specs for build caches available on mirrors
    """
    if not cache_manager.spec_cache:
        cache_manager.update_local_index_cache()
        cache_manager.regenerate_spec_cache()

    return cache_manager.get_all_built_specs()


def get_keys(install=False, trust=False, force=False):
    """
    Get pgp public keys available on mirror
    with suffix .key or .pub
    """
    if not spack.mirror.MirrorCollection():
        tty.die("Please add a spack mirror to allow " +
                "download of build caches.")

    keys = set()

    for mirror in spack.mirror.MirrorCollection().values():
        fetch_url_build_cache = url_util.join(
            mirror.fetch_url, _build_cache_relative_path)

        mirror_dir = url_util.local_file_path(fetch_url_build_cache)
        if mirror_dir:
            tty.debug('Finding public keys in {0}'.format(mirror_dir))
            files = os.listdir(str(mirror_dir))
            for file in files:
                if re.search(r'\.key', file) or re.search(r'\.pub', file):
                    link = url_util.join(fetch_url_build_cache, file)
                    keys.add(link)
        else:
            tty.debug('Finding public keys at {0}'
                      .format(url_util.format(fetch_url_build_cache)))
            # For s3 mirror need to request index.html directly
            p, links = web_util.spider(
                url_util.join(fetch_url_build_cache, 'index.html'))

            for link in links:
                if re.search(r'\.key', link) or re.search(r'\.pub', link):
                    keys.add(link)

        for link in keys:
            with Stage(link, name="build_cache", keep=True) as stage:
                if os.path.exists(stage.save_filename) and force:
                    os.remove(stage.save_filename)
                if not os.path.exists(stage.save_filename):
                    try:
                        stage.fetch()
                    except fs.FetchError:
                        continue
            tty.debug('Found key {0}'.format(link))
            if install:
                if trust:
                    Gpg.trust(stage.save_filename)
                    tty.debug('Added this key to trusted keys.')
                else:
                    tty.debug('Will not add this key to trusted keys.'
                              'Use -t to install all downloaded keys')


def needs_rebuild(spec, mirror_url, rebuild_on_errors=False):
    if not spec.concrete:
        raise ValueError('spec must be concrete to check against mirror')

    pkg_name = spec.name
    pkg_version = spec.version

    pkg_hash = spec.dag_hash()
    pkg_full_hash = spec.full_hash()

    tty.debug('Checking {0}-{1}, dag_hash = {2}, full_hash = {3}'.format(
        pkg_name, pkg_version, pkg_hash, pkg_full_hash))
    tty.debug(spec.tree())

    # Try to retrieve the .spec.yaml directly, based on the known
    # format of the name, in order to determine if the package
    # needs to be rebuilt.
    cache_prefix = build_cache_prefix(mirror_url)
    spec_yaml_file_name = tarball_name(spec, '.spec.yaml')
    file_path = os.path.join(cache_prefix, spec_yaml_file_name)

    result_of_error = 'Package ({0}) will {1}be rebuilt'.format(
        spec.short_spec, '' if rebuild_on_errors else 'not ')

    try:
        _, _, yaml_file = web_util.read_from_url(file_path)
        yaml_contents = codecs.getreader('utf-8')(yaml_file).read()
    except (URLError, web_util.SpackWebError) as url_err:
        err_msg = [
            'Unable to determine whether {0} needs rebuilding,',
            ' caught exception attempting to read from {1}.',
        ]
        tty.error(''.join(err_msg).format(spec.short_spec, file_path))
        tty.debug(url_err)
        tty.warn(result_of_error)
        return rebuild_on_errors

    if not yaml_contents:
        tty.error('Reading {0} returned nothing'.format(file_path))
        tty.warn(result_of_error)
        return rebuild_on_errors

    spec_yaml = syaml.load(yaml_contents)

    # If either the full_hash didn't exist in the .spec.yaml file, or it
    # did, but didn't match the one we computed locally, then we should
    # just rebuild.  This can be simplified once the dag_hash and the
    # full_hash become the same thing.
    if ('full_hash' not in spec_yaml or
            spec_yaml['full_hash'] != pkg_full_hash):
        if 'full_hash' in spec_yaml:
            reason = 'hash mismatch, remote = {0}, local = {1}'.format(
                spec_yaml['full_hash'], pkg_full_hash)
        else:
            reason = 'full_hash was missing from remote spec.yaml'
        tty.msg('Rebuilding {0}, reason: {1}'.format(
            spec.short_spec, reason))
        tty.msg(spec.tree())
        return True

    return False


def check_specs_against_mirrors(mirrors, specs, output_file=None,
                                rebuild_on_errors=False):
    """Check all the given specs against buildcaches on the given mirrors and
    determine if any of the specs need to be rebuilt.  Reasons for needing to
    rebuild include binary cache for spec isn't present on a mirror, or it is
    present but the full_hash has changed since last time spec was built.

    Arguments:
        mirrors (dict): Mirrors to check against
        specs (iterable): Specs to check against mirrors
        output_file (string): Path to output file to be written.  If provided,
            mirrors with missing or out-of-date specs will be formatted as a
            JSON object and written to this file.
        rebuild_on_errors (boolean): Treat any errors encountered while
            checking specs as a signal to rebuild package.

    Returns: 1 if any spec was out-of-date on any mirror, 0 otherwise.

    """
    rebuilds = {}
    for mirror in spack.mirror.MirrorCollection(mirrors).values():
        tty.debug('Checking for built specs at {0}'.format(mirror.fetch_url))

        rebuild_list = []

        for spec in specs:
            if needs_rebuild(spec, mirror.fetch_url, rebuild_on_errors):
                rebuild_list.append({
                    'short_spec': spec.short_spec,
                    'hash': spec.dag_hash()
                })

        if rebuild_list:
            rebuilds[mirror.fetch_url] = {
                'mirrorName': mirror.name,
                'mirrorUrl': mirror.fetch_url,
                'rebuildSpecs': rebuild_list
            }

    if output_file:
        with open(output_file, 'w') as outf:
            outf.write(json.dumps(rebuilds))

    return 1 if rebuilds else 0


def _download_buildcache_entry(mirror_root, descriptions):
    for description in descriptions:
        description_url = os.path.join(mirror_root, description['url'])
        path = description['path']
        fail_if_missing = description['required']

        mkdirp(path)

        stage = Stage(
            description_url, name="build_cache", path=path, keep=True)

        try:
            stage.fetch()
        except fs.FetchError as e:
            tty.debug(e)
            if fail_if_missing:
                tty.error('Failed to download required url {0}'.format(
                    description_url))
                return False

    return True


def download_buildcache_entry(file_descriptions, mirror_url=None):
    if not mirror_url and not spack.mirror.MirrorCollection():
        tty.die("Please provide or add a spack mirror to allow " +
                "download of buildcache entries.")

    if mirror_url:
        mirror_root = os.path.join(
            mirror_url, _build_cache_relative_path)
        return _download_buildcache_entry(mirror_root, file_descriptions)

    for mirror in spack.mirror.MirrorCollection().values():
        mirror_root = os.path.join(
            mirror.fetch_url,
            _build_cache_relative_path)

        if _download_buildcache_entry(mirror_root, file_descriptions):
            return True
        else:
            continue

    return False
