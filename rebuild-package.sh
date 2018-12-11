#!/bin/bash

###
### The following environment variables are expected to be set in order for
### the various elements in this script to function properly.  Listed first
### are two defaults we rely on from gitlab, then three we set up in the
### variables section of gitlab ourselves, and finally four variables
### written into the .gitlab-ci.yml file.
###
### CI_PROJECT_DIR
### CI_JOB_NAME
###
### AWS_ACCESS_KEY_ID
### AWS_SECRET_ACCESS_KEY
### SPACK_SIGNING_KEY
###
### CDASH_BASE_URL
### ROOT_SPEC
### DEPENDENCIES
### MIRROR_URL
###

### Variables ##################################################################
export FORCE_UNSAFE_CONFIGURE=1

TEMP_DIR="${CI_PROJECT_DIR}/jobs_scratch_dir"

JOB_LOG_DIR="${TEMP_DIR}/logs"
SPEC_DIR="${TEMP_DIR}/specs"
CURRENT_WORKING_DIR=`pwd`
LOCAL_MIRROR="${CURRENT_WORKING_DIR}/local_mirror"
BUILD_CACHE_DIR="${LOCAL_MIRROR}/build_cache"
SPACK_BIN_DIR="${CI_PROJECT_DIR}/bin"
CDASH_UPLOAD_URL="${CDASH_BASE_URL}/submit.php?project=Spack"
DEP_JOB_RELATEBUILDS_URL="${CDASH_BASE_URL}/api/v1/relateBuilds.php"
declare -a JOB_DEPS_PKG_NAMES

export SPACK_ROOT=${CI_PROJECT_DIR}
export PATH="${SPACK_BIN_DIR}:${PATH}"
export GNUPGHOME="${CURRENT_WORKING_DIR}/opt/spack/gpg"
################################################################################

### Functions ##################################################################
report_to_cdash() {
    echo "The full log file is located at $JOB_LOG_DIR/cdash_log.txt"
    # TODO: send this log data to cdash!
}

log_command() {
    mkfifo "$TEMP_DIR/code.fifo"
    eval "exec 9<>$TEMP_DIR/code.fifo"
    unlink "$TEMP_DIR/code.fifo"

    local DASHES='--------------------'
    DASHES="${DASHES}${DASHES}${DASHES}${DASHES}"

    echo "$DASHES"

    local arg0="$1"
    if [ "${arg0::1}" '=' ':' ] ; then
        local comment="$1" ; shift
        echo "(${comment:1})"
    fi

    local line="RUN:"
    local token
    local first=1
    for token in "$@" ; do
        if [[ "$token" =~ ' ' ]] ; then
            token="\"$token\""
        fi

        local n="${#line}"
        if [ "$n" -gt '0' ] ; then
            n="$(( n + ${#token} ))"
        fi

        if [ "$n" -gt 72 ] ; then
            if [ "$first" '=' '1' ] ; then
                first=0
            else
                echo ' \'
            fi

            if [ "${#line}" -gt 72 ] ; then
                local frag
                local rem

                rem="$line"

                line=""
                local last=0
                while true ; do
                    frag="${rem/ */ }" ; rem="${rem:${#frag}}"

                    local m="${#line}"
                    if [ "$m" -gt 14 ] ; then
                        m="$(( m + ${#frag} ))"
                    fi

                    while [ "$m" -gt 68 ] ; do
                        local portion="${line:68}"
                        line="${line::68}"
                        echo "${line}..."
                        line="              ${portion}"
                        m="$(( ${#line} + ${#frag} ))"
                    done

                    if [ "$last" '=' '0' ] ; then
                        line="${line}${frag}"
                    fi

                    if [ "$last" '=' '0' ] ; then
                        if [ -z "$rem" ] ; then
                            last=1
                        fi
                        continue
                    fi

                    echo -n "$line"
                    break
                done
            else
                echo -n "$line"
            fi
            line='    '
        fi
        line="${line} ${token}"
    done

    if [ -n "$line" ] ; then
        if [ "$first" '!=' '1' ] ; then
            echo ' \'
        fi
        echo -n "$line"
    fi
    echo

    echo "$DASHES"

    (
      (
        (
          (
            (
              (
                "$@"
                echo $? >&9
              ) || true
            ) | sed 's/^/   | /g'
          ) 3>&2 2>&1 >&3
        ) | sed 's/^/  >> /g'
      ) 3>&2 2>&1 >&3
    )

    local res
    read -u 9 res

    echo "$DASHES"
    echo "exit code: $res"
    echo "$DASHES"

    exec 9<&-

    return $res
}

extract_build_id() {
    LINES_TO_SEARCH=$1
    regex="buildSummary\.php\?buildid=([[:digit:]]+)"
    SINGLE_LINE_OUTPUT=$(echo ${LINES_TO_SEARCH} | tr -d '\n')

    if [[ ${SINGLE_LINE_OUTPUT} =~ ${regex} ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo "NONE"
    fi
}

main() {
    set -e

    local tokens=($CI_JOB_NAME)
    local pkgName="${tokens[0]}"
    local pkgVersion="${tokens[1]}"
    local compiler="${tokens[2]}"
    local osarch="${tokens[3]}"

    JOB_SPEC_NAME="${pkgName}@${pkgVersion}%${compiler} arch=${osarch}"
    JOB_PKG_NAME="${pkgName}"
    SPEC_YAML_PATH="${SPEC_DIR}/${pkgName}.yaml"
    local root_spec_name="${ROOT_SPEC} arch=${osarch}"
    local spec_names_to_save="${pkgName}"

    _old_ifs="$IFS" ; IFS=';'
    local deps=(${DEPENDENCIES})
    IFS="$_old_ifs"

    for dep in "${deps[@]}"; do
        tokens=(${dep})
        pkgName="${tokens[0]}"
        spec_names_to_save="${spec_names_to_save} ${pkgName}"
        JOB_DEPS_PKG_NAMES+=("${pkgName}")
    done

    log_command                             \
        spack -d buildcache save-yaml       \
            --specs "${spec_names_to_save}" \
            --root-spec "${root_spec_name}" \
            --yaml-dir "${SPEC_DIR}"

    echo
    echo "Building package ${JOB_SPEC_NAME}, ${HASH}, ${MIRROR_URL}"
    echo

    log_command ':Show compilers' spack compilers
    log_command ':Compiler Configuration' \
        cat ~/.spack/linux/compilers.yaml
    log_command ':Ensure build cache directory' \
        mkdir -p "${BUILD_CACHE_DIR}"

    # Get buildcache name so we can write a CDash build id file in the right
    # place.  If we're unable to get the buildcache name, we may have
    # encountered a problem concretizing the spec, or some other issue that will
    # eventually cause the job to fail.
    JOB_BUILD_CACHE_ENTRY_NAME="$(
        spack buildcache get-buildcache-name \
            --spec-yaml "${SPEC_YAML_PATH}" )"

    log_command ':Initialize GPG' spack gpg list

    (

        # make sure we don't leak the key!
        set +x
        echo ${SPACK_SIGNING_KEY} | base64 --decode

    # discard stderr output, just in case!
    ) 2>/dev/null \
    | log_command ':Import Signing Key' gpg2 --import

    # ultimately trust all keys in the keyring
    # (there should only be one, anyway)
    gpg --export-ownertrust                         \
        | sed 's/\([A-F0-9]\{40\}\):[1-5]:/\1:5:/g' \
        | log_command gpg --import-ownertrust

    log_command spack gpg list --trusted
    log_command spack gpg list --signing

    if log_command ':Check remote mirror'           \
            spack -d buildcache check               \
            --spec-yaml "${SPEC_YAML_PATH}"         \
            --mirror-url "${MIRROR_URL}" --no-index
    then
        echo
        echo "Already up-to-date: ${JOB_SPEC_NAME}"
        echo

        log_command ':Configure remote mirror' \
            spack mirror add remote_binary_mirror ${MIRROR_URL}

        log_command ':Download from remote mirror' \
            spack -d buildcache download           \
                --spec-yaml "${SPEC_YAML_PATH}"    \
                --path "${BUILD_CACHE_DIR}/"
    else
        echo
        echo "Needs build: ${JOB_SPEC_NAME}"
        echo

        log_command ':Configure local mirror' \
            spack mirror add local_artifact_mirror "file://${LOCAL_MIRROR}"

        JOB_CDASH_ID="NONE"

        # Install package, using the buildcache from the local mirror to
        # satisfy dependencies.

        log_command ':Build & Install package'           \
            spack -d -k install                          \
                --use-cache                              \
                --cdash-upload-url "${CDASH_UPLOAD_URL}" \
                --cdash-build "${JOB_SPEC_NAME}"         \
                --cdash-site "Spack AWS Gitlab Instance" \
                --cdash-track "Experimental"             \
                -f "${SPEC_YAML_PATH}"                   \
        | tee >( grep -h "buildSummary\\.php" > "$TEMP_DIR/build-id-line.txt" )

        BUILD_ID_LINE="$( head -n 1 "$TEMP_DIR/build-id-line.txt" )"

        # By parsing the output of the "spack install" command, we can get the
        # buildid generated for us by CDash
        JOB_CDASH_ID=$(extract_build_id "${BUILD_ID_LINE}")

        log_command ':Check CDash ID' \
            eval '[ -n "$JOB_CDASH_ID" ] && echo "$JOB_CDASH_ID"'

        # Create buildcache entry for this package.  We should eventually change
        # this to read the spec from the yaml file, but it seems unlikely there
        # will be a spec that matches the name which is NOT the same as
        # represented in the yaml file
        log_command ':Create build cache entry'       \
            spack -d buildcache create                \
                --spec-yaml "${SPEC_YAML_PATH}" -a -f \
                -d "${LOCAL_MIRROR}"                  \
                --cdash-build-id "${JOB_CDASH_ID}"

        # TODO: Now push buildcache entry to remote mirror, something like:
        # "spack buildcache put <mirror> <spec>", when that subcommand
        # is implemented
        log_command ':Upload build cache entry' \
            spack -d upload-s3 spec             \
                --base-dir "${LOCAL_MIRROR}"    \
                --spec-yaml "${SPEC_YAML_PATH}"
    fi

    # Now, whether we had to build the spec or download it pre-built, we should
    # have the cdash build id file sitting in place as well.  We use it to link
    # this job to the jobs it depends on in CDash.
    JOB_CDASH_ID_FILE="${BUILD_CACHE_DIR}/${JOB_BUILD_CACHE_ENTRY_NAME}.cdashid"

    log_command ':Check CDash ID File' \
        eval '[ -f "${JOB_CDASH_ID_FILE}" ]'

    JOB_CDASH_BUILD_ID=$(<${JOB_CDASH_ID_FILE})
    log_command ':Check CDash ID' \
        eval '[ "${JOB_CDASH_BUILD_ID}" '"'!='"' "NONE" ]'

    # Now get CDash ids for dependencies and "relate" each dependency build
    # with this jobs build
    for DEP_PKG_NAME in "${JOB_DEPS_PKG_NAMES[@]}"; do
        echo "Getting cdash id for dependency --> ${DEP_PKG_NAME} <--"
        DEP_SPEC_YAML_PATH="${SPEC_DIR}/${DEP_PKG_NAME}.yaml"

        echo "dependency spec name = ${DEP_PKG_NAME}," \
             "spec yaml saved to ${DEP_SPEC_YAML_PATH}"

        DEP_JOB_BUILDCACHE_NAME="$(
            spack -d buildcache get-buildcache-name \
                --spec-yaml "${DEP_SPEC_YAML_PATH}" )"

        log_command ":Check build cache entry ($DEP_SPEC_NAME)" \
            eval 'echo "$DEP_JOB_BUILDCACHE_NAME"'

        DEP_JOB_ID_FILE="${BUILD_CACHE_DIR}/${DEP_JOB_BUILDCACHE_NAME}"
        DEP_JOB_ID_FILE="${DEP_JOB_ID_FILE}.cdashid"

        log_command ":Check cdashid file ($DEP_SPEC_NAME)" \
            [ -f "${DEP_JOB_ID_FILE}" ]

        DEP_JOB_CDASH_BUILD_ID=$(<${DEP_JOB_ID_FILE})
        echo "File ${DEP_JOB_ID_FILE} contained value
        ${DEP_JOB_CDASH_BUILD_ID}"

        echo "Relating builds -> ${JOB_SPEC_NAME}
        (buildid=${JOB_CDASH_BUILD_ID}) depends on ${DEP_PKG_NAME}
        (buildid=${DEP_JOB_CDASH_BUILD_ID})"

        local post_body='{"project":"Spack","relationship":"depends on",'
        post_body="${post_body}"'"buildid":'"${JOB_CDASH_BUILD_ID},"
        post_body="${post_body}"'"relatedid":'$DEP_JOB_CDASH_BUILD_ID'}'

        log_command ':Post dependency info to CDash' \
            curl "${DEP_JOB_RELATEBUILDS_URL}"       \
            -H "Content-Type: application/json"      \
            -H "Accept: application/json"            \
            -d "$post_body"
    done
}

_finalized=0
finalize() {
    if [ "$_finalized" '=' '1' ] ; then
        return
    fi
    report_to_cdash
    local res="$?"
    if [ "$res" '=' '0' ] ; then
        _finalized=1
    fi

    return $res
}

################################################################################

trap "finalize ; exit" INT TERM QUIT EXIT
mkdir -p ${JOB_LOG_DIR}
mkdir -p ${SPEC_DIR}

redirect=1
if [ "$redirect" '=' '1' ] ; then
    main &> "$JOB_LOG_DIR/cdash_log.txt"
else
    main
fi

du -sh ${BUILD_CACHE_DIR}
find ${BUILD_CACHE_DIR} -maxdepth 3 -type d -ls
