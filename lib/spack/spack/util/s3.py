# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os

import six.moves.urllib.parse as urllib_parse
import six

import spack


def create_s3_session(url):
    if isinstance(url, six.string_types):
        url = urllib_parse.urlparse(
            url,
            scheme='file',
            allow_fragments=False)

    if url.scheme != 's3':
        raise ValueError(
            'Can not create S3 session from URL with scheme: {}'.format(
                url.scheme))

    # NOTE(opadron): import boto and friends as late as possible.  We don't
    # want to require boto as a dependency unless the user actually wants to
    # access S3 mirrors.
    from boto3 import Session

    session = Session()

    s3_client_args = {"use_ssl": spack.config.get('config:verify_ssl')}

    endpoint_url = os.environ.get('AWS_ENDPOINT_URL')
    if endpoint_url:
        if urllib_parse.urlparse(
                endpoint_url,
                scheme=None,
                allow_fragments=False).scheme is None:
            endpoint_url = '://'.join(('https', endpoint_url))

        s3_client_args['endpoint_url'] = endpoint_url

    # if no access credentials provided above, then access anonymously
    if not session.get_credentials():
        from botocore import UNSIGNED
        from botocore.client import Config

        s3_client_args["config"] = Config(signature_version=UNSIGNED)

    return session.client('s3', **s3_client_args)
