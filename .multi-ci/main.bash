#! /usr/bin/env bash

set -e

if [ -z "$DOWNSTREAM_CI_REPO" ] ; then
    echo "Warning: missing variable: DOWNSTREAM_CI_REPO" >&2
fi

apt-get -qyy update && apt-get -qyy install git

git config --global user.email "robot@spack.io"
git config --global user.name "Mr. Roboto"

# TODO: do more interesting dynamic st00f here
echo "Generating .gitlab-ci.yml"
echo
tee .gitlab-ci.yml << EOF
hello:
  script:
    - "./run-this-script.sh"
  tags:
    - "spack-k8s"
  image: "ubuntu"
  variables:
    SHA: "$CI_COMMIT_SHA"
EOF

echo "Generating ./run-this-script.sh"
echo
tee ./run-this-script.sh << EOF
#! /usr/bin/env sh

echo "Upstream SHA: \$SHA"
echo "Hello, from second CI level! ^___^"
EOF

chmod +x ./run-this-script.sh

git status

current_branch="$CI_COMMIT_REF_NAME"
downstream_branch="auto-ci-$current_branch"

git branch -D ___multi_ci___ 2> /dev/null || true
git checkout -b ___multi_ci___
git add .gitlab-ci.yml ./run-this-script.sh
git commit -m 'x'
git reset "$( git commit-tree HEAD^{tree} -m x )"
git status
git push --force "$DOWNSTREAM_CI_REPO" \
    "___multi_ci___:$downstream_branch"

