#! /usr/bin/env bash

set -e

if [ -z "$DOWNSTREAM_CI_REPO" ] ; then
    echo "Warning: missing variable: DOWNSTREAM_CI_REPO" >&2
fi

git config --global user.email "robot@spack.io"
git config --global user.name "Mr. Roboto"

current_branch="$CI_COMMIT_REF_NAME"

original_directory=$(pwd)

workdir=$(mktemp -d)
cd $workdir

micro_service_url="https://internal.spack.io/glciy/${CI_COMMIT_SHA}.yaml"
wget micro_service_url
yaml_file="${workdir}/${CI_COMMIT_SHA}.yaml"

cp "$yaml_file" "${original_directory}/"

py_script="${original_directory}/bin/create-buildgroups.py"

#$($py_script --branch $current_branch )

# git status

# downstream_branch="auto-ci-$current_branch"

# git branch -D ___multi_ci___ 2> /dev/null || true
# git checkout -b ___multi_ci___
# git add .gitlab-ci.yml
# git commit -m 'x'
# git reset "$( git commit-tree HEAD^{tree} -m x )"
# git status
## git push --force "$DOWNSTREAM_CI_REPO" \
##     "___multi_ci___:$downstream_branch"
# git push --force "$DOWNSTREAM_CI_REPO" \
#     "___multi_ci___:$current_branch"

