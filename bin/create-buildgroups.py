#!/usr/bin/env python

import argparse
import requests
import sys

"""
import os, re

from ruamel.yaml import YAML

yaml = YAML(typ='safe')

job_name_regex = re.compile('([^\\s]+)\\s+([^\\s]+)\\s+([^\\s]+)\\s+([^\\s]+)\\s+([^\\s]+)')

with open('/work/wget_test/enhance-release-spec-set.yaml', 'r') as fd:
    ci_jobs = yaml.load(fd.read())

for job_name, job_entry in ci_jobs.items():
    m = job_name_regex.search(job_name)
    if m:
        print('job -> release tag: {0}, pkg: {1}, version: {2}, compiler: {3}, os/arch: {4}'.format(
            m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)))
    else:
        print('something else -> {0}'.format(job_name))
"""

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="CDash BuildGroup Creator")
  requiredNamed = parser.add_argument_group("required named arguments")
  requiredNamed.add_argument("-b", "--branch",
      help="The name of the Git branch that will populate this BuildGroup",
      required=True)
  requiredNamed.add_argument("-c", "--credentials",
      help="path to file containing a CDash authentication token",
      required=True)
  requiredNamed.add_argument("-f", "--buildfile",
      help="Text file containing a list of expected build names (one per line)",
      required=True)
  requiredNamed.add_argument("-p", "--projectid",
      help="The Id of the project in CDash that you'd like to modify",
      required=True)
  requiredNamed.add_argument("-s", "--siteid",
      help="The Id of the site in CDash that will be submitting the builds",
      required=True)
  requiredNamed.add_argument("-u", "--cdash-url",
      help="CDash base URL (index.php removed)",
      required=True)
  args = parser.parse_args()

  with open(args.credentials, "r") as auth_file, open(args.buildfile, "r") as build_file:
    # Create the BuildGroup and a corresponding "Latest" BuildGroup.
    auth_token = auth_file.read().strip()
    headers = {"Authorization": "Bearer {0}".format(auth_token)}
    url = "{0}/api/v1/buildgroup.php".format(args.cdash_url)

    def create_buildgroup(args, headers, url, group_name, group_type):
      payload = {
        "newbuildgroup": group_name,
        "projectid": args.projectid,
        "type": group_type
      }
      r = requests.post(url, json=payload, headers=headers)
      if not r.ok:
        print("Problem creating '{0}' group: {1} / {2}\n".format(group_name, r.status_code, r.text))
        sys.exit(1)
      return r.json()['id']

    daily_groupid = create_buildgroup(args, headers, url, args.branch, "Daily")
    latest_groupid = create_buildgroup(args, headers, url, "Latest {0}".format(args.branch), "Latest")

    # Populate the 'Latest' BuildGroup with our list of expected builds.
    for build_name in build_file:
      build_name = build_name.strip()

      payload = {
        "match": build_name,
        "projectid": args.projectid,
        "buildgroup": {"id": daily_groupid},
        "dynamic": {"id": latest_groupid},
        "site": {"id": args.siteid}
      }
      r = requests.post(url, json=payload, headers=headers)
      if not r.ok:
        print("Problem creating dynamic row for '{0}': {1} / {2}\n".format(build_name, r.status_code, r.text))
        sys.exit(1)
    print("Success")