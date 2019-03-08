#! /usr/bin/env bash

if [ -z "$DOWNSTREAM_CI_REPO" ] ; then
    echo "Warning: missing variable: DOWNSTREAM_CI_REPO" >&2
fi

SHA="$( git rev-parse HEAD )"

# TODO: do more interesting dynamic st00f here
echo "Generating .gitlab-ci.yml"
echo
tee .gitlab-ci.yml << EOF
hello:
  script:
    - "./run-this-script.sh"
  tags:
    - "spack-k8s"
  image: "busybox"
  variables:
    - SHA="$SHA"
EOF

echo "Generating ./run-this-script.sh"
echo
tee ./run-this-script.sh << EOF
#! /usr/bin/env sh

echo "Upstream SHA: \$SHA"
echo "Hello, from second CI level! ^___^"
EOF

chmod +x ./run-this-script.sh

current_branch="$( git rev-parse --abbrev-ref HEAD )"
downstream_branch="auto-ci-$current_branch"

git add .gitlab-ci.yml ./run-this-script.sh
git commit -m 'x'
git reset "$( git commit-tree HEAD^{tree} -m x )"
git push "$DOWNSTREAM_CI_REPO" "$current_branch:$downstream_branch"

