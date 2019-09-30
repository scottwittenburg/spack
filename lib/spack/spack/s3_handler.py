# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import itertools as it

from io import BufferedReader

import six.moves.urllib.response as urllib_response
import six.moves.urllib.request as urllib_request
import six.moves.urllib.parse as urllib_parse
import six.moves.urllib.error as urllib_error

import spack

import spack.util.s3 as s3_util
import spack.util.url as url_util
import spack.util.web as web_util


# NOTE(opadron): Workaround issue in boto where its StreamingBody implementation
# is missing several APIs expected from IOBase.  These missing APIs prevent the
# streams returned by boto from being passed as-are along to urllib.
#
# https://github.com/boto/botocore/issues/879
# https://github.com/python/cpython/pull/3249
class WrapStream(BufferedReader):
    def __init__(self, raw):
        raw.readable = lambda: True
        raw.writable = lambda: False
        raw.seekable = lambda: False
        raw.closed = False
        raw.flush = lambda: None
        super(WrapStream, self).__init__(raw)

    def detach(self):
        self.raw = None

    def read(self, *args, **kwargs):
        return self.raw.read(*args, **kwargs)

    def __getattr__(self, key):
        return getattr(self.raw, key)


def _s3_open(url):
    parsed = urllib_parse.urlparse(url, scheme='file', allow_fragments=False)
    s3 = s3_util.create_s3_session(parsed)

    bucket = parsed.netloc
    key = parsed.path

    if key.startswith('/'):
        key = key[1:]

    obj = s3.get_object(Bucket=bucket, Key=key)

    # NOTE(opadron): Apply workaround here (see above)
    stream = WrapStream(obj['Body'])
    headers = web_util.standardize_header_names(
            obj['ResponseMetadata']['HTTPHeaders'])

    return url, headers, stream


class UrllibS3Handler(urllib_request.HTTPSHandler):
    def s3_open(self, req):
        orig_url = req.get_full_url()
        from botocore.exceptions import ClientError
        try:
            url, headers, stream = _s3_open(orig_url)
            return urllib_response.addinfourl(stream, headers, url)
        except ClientError as err:
            # if no such [KEY], but [KEY]/index.html exists,
            # return that, instead.
            if err.response['Error']['Code'] == 'NoSuchKey':
                try:
                    _, headers, stream = _s3_open(
                            url_util.join(orig_url, 'index.html'))
                    return urllib_response.addinfourl(
                            stream, headers, orig_url)

                except ClientError as err2:
                    if err.response['Error']['Code'] == 'NoSuchKey':
                        # raise original error
                        raise urllib_error.URLError(err)

                    raise urllib_error.URLError(err2)

            raise urllib_error.URLError(err)


S3OpenerDirector = urllib_request.build_opener(UrllibS3Handler())

open = S3OpenerDirector.open

