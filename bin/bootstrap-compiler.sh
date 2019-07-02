#!/bin/bash



# Write the Gitlab Container Registry credentials in the expected location
echo "{\"auths\":{\"$CI_REGISTRY\":{\"username\":\"${CI_REGISTRY_USER}\",\"password\":\"${CI_REGISTRY_PASSWORD}\"}}}" > /kaniko/.docker/config.json

CONTEXT_PATH="share/spack/templates/docker"
DOCKERFILE_NAME="ci-compiler-bootstrap.Dockerfile"
DOCKERFILE_CONTEXT="${CI_PROJECT_DIR}/${CONTEXT_PATH}"
DOCKERFILE="${DOCKERFILE_CONTEXT}/${DOCKERFILE_NAME}"

# Build the image and tag/push to Gitlab Container Registry
/kaniko/executor \
    --context ${DOCKERFILE_CONTEXT} \
    --dockerfile ${DOCKERFILE} \
    --build-arg BASE_BUILDER_IMAGE=${BASE_BUILDER_IMAGE} \
    --build-arg BINARY_MIRROR_URL=${BINARY_MIRROR_URL} \
    --build-arg COMPILER_TO_BOOTSTRAP=${COMPILER_TO_BOOTSTRAP} \
    --destination ${CI_REGISTRY_IMAGE}:${CI_COMMIT_TAG}
