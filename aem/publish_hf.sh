#!/usr/bin/env bash

###############################################################################
# Publish to the Hugging Face dataset APOLLO_HF_REPO, branch APOLLO_HF_REVISION
# (aem/env.sh), what aem restores from it when it creates a container,
# instead of downloading it from the Baidu servers:
#
#   image              the dev image (docker save)
#   neo-deps           the packages buildtool installed in /opt/apollo/neo
#   buildtool-index    buildtool's package index
#   perception-models  the model weights in modules/perception/data/models
#   bazel-repo-cache   Bazel's repository cache (.cache/repos)
#
#   bash aem/publish_hf.sh [<name>...]    # all of them when none is given
#
# Run on the host after a successful full build in the container. Needs
# `hf auth login` as an account that can write APOLLO_HF_REPO.
###############################################################################

set -eo pipefail

AEM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
WORKSPACE="$(cd "${AEM_DIR}/.." && pwd -P)"
source "${AEM_DIR}/env.sh"
set -u

if [[ -z "${APOLLO_HF_REPO}" ]]; then
  echo "APOLLO_HF_REPO is not set (aem/env.sh)" >&2
  exit 1
fi
OWNER="${APOLLO_HF_REPO%%/*}"
ACCOUNTS="$(hf auth whoami --format json | python3 -c '
import json, sys
me = json.load(sys.stdin)
names = [me.get("user") or ""]
names += [o if isinstance(o, str) else o.get("name", "") for o in me.get("orgs") or []]
print(" ".join(names))')"
if [[ " ${ACCOUNTS} " != *" ${OWNER} "* ]]; then
  echo "logged in to Hugging Face as '${ACCOUNTS}', which cannot write ${APOLLO_HF_REPO};" \
    "use \`hf auth login\` or \`hf auth switch\`" >&2
  exit 1
fi

hf repos branch create "${APOLLO_HF_REPO}" "${APOLLO_HF_REVISION}" --repo-type dataset --exist-ok

CONTAINER="${APOLLO_ENV_CONTAINER_NAME}"
IMAGE="$(docker inspect -f '{{.Config.Image}}' "${CONTAINER}")"
TAG="${IMAGE##*:}"
WORK="${WORKSPACE}/.cache/hf-publish"
mkdir -p "${WORK}"

check_private() {
  # Refuse to publish an archive that names this machine or its user, in
  # its file names or contents
  local file="$1"
  # grep -q stops at the first match: the pipeline's status must be grep's
  local -
  set +o pipefail
  local patterns=(-e "${HOME}" -e "${WORKSPACE}" -e "$(hostname)")
  [[ ${#USER} -ge 4 ]] && patterns+=(-e "${USER}")
  echo "[publish] checking $(basename "${file}") for local user and machine names ..."
  if tar -tzf "${file}" | grep -qF "${patterns[@]}" ||
    pigz -dc "${file}" | grep -aqF "${patterns[@]}"; then
    echo "${file} names the local user or machine; not uploaded" >&2
    exit 1
  fi
}

upload() {
  local file="$1"
  local name
  name="$(basename "${file}")"
  check_private "${file}"
  ls -lh "${file}"
  hf upload "${APOLLO_HF_REPO}" "${file}" "${name}" --repo-type dataset \
    --revision "${APOLLO_HF_REVISION}"
  rm -f "${file}"
  echo "[publish] https://huggingface.co/datasets/${APOLLO_HF_REPO}/resolve/${APOLLO_HF_REVISION}/${name}"
}

publish_image() {
  local name="${IMAGE##*/}"
  echo "[publish] ${IMAGE}"
  docker save "${IMAGE}" | pigz -p "$(nproc)" > "${WORK}/${name/:/-}.tar.gz"
  upload "${WORK}/${name/:/-}.tar.gz"
}

publish_neo_deps() {
  # Only the apollo-neo-* packages listed by neo_deps_state.py: what buildtool
  # built from source and installed in /opt/apollo/neo stays out
  local name="neo-deps-${TAG}.tar.gz"
  echo "[publish] packages installed after ${IMAGE}"
  docker run --rm --entrypoint cat "${IMAGE}" /var/lib/dpkg/status > "${WORK}/dpkg_baseline"
  docker cp "${WORK}/dpkg_baseline" "${CONTAINER}:/tmp/neo_deps_dpkg_baseline"
  rm -f "${WORK}/dpkg_baseline"
  docker exec -u root "${CONTAINER}" python3 /opt/apollo/aem/neo_deps_state.py save \
    --baseline /tmp/neo_deps_dpkg_baseline --version "${name}"
  docker exec -u root "${CONTAINER}" rm -f /tmp/neo_deps_dpkg_baseline
  docker exec -u root "${CONTAINER}" tar -C /opt/apollo/neo \
    --owner=0 --group=0 --numeric-owner --no-recursion \
    -T /opt/apollo/neo/.neo_deps/files -cf - | pigz -p "$(nproc)" > "${WORK}/${name}"
  upload "${WORK}/${name}"
}

publish_buildtool_index() {
  local name="buildtool-index-${TAG}.tar.gz"
  echo "[publish] buildtool's package index"
  docker exec -u root "${CONTAINER}" bash -c \
    "ls /opt/apollo/neo/share/buildtool/namespaces/*/Packages > /dev/null"
  docker exec -u root "${CONTAINER}" tar -C /opt/apollo/neo/share/buildtool \
    --owner=0 --group=0 --numeric-owner -cf - namespaces | pigz -p "$(nproc)" > "${WORK}/${name}"
  upload "${WORK}/${name}"
}

publish_perception_models() {
  # The files git does not track (BUILD, README and cyberfile.xml are in git);
  # TensorRT builds TRTengine.cache for the local GPU on first use
  local name="perception-models-${TAG}.tar.gz"
  echo "[publish] perception models"
  (
    cd "${WORKSPACE}"
    git ls-files -o -- modules/perception/data/models |
      grep -v -e '/TRTengine\.cache$' -e '/__MACOSX/' > "${WORK}/model_files"
    tar --owner=0 --group=0 --numeric-owner -T "${WORK}/model_files" -cf - |
      pigz -p "$(nproc)" > "${WORK}/${name}"
  )
  echo "[publish] $(wc -l < "${WORK}/model_files") files"
  rm -f "${WORK}/model_files"
  upload "${WORK}/${name}"
}

publish_bazel_repo_cache() {
  # Only the downloaded archives, not the per-run state
  local name="bazel-repo-cache-${TAG}.tar.gz"
  local cache="${WORKSPACE}/.cache/repos"
  echo "[publish] $(ls "${cache}/content_addressable/sha256" | wc -l) archives of the Bazel repository cache"
  tar -C "${cache}" --owner=0 --group=0 --numeric-owner -cf - content_addressable |
    pigz -p "$(nproc)" > "${WORK}/${name}"
  upload "${WORK}/${name}"
}

main() {
  local names=("$@")
  [[ ${#names[@]} -gt 0 ]] ||
    names=(image neo-deps buildtool-index perception-models bazel-repo-cache)
  local name
  for name in "${names[@]}"; do
    case "${name}" in
      image | neo-deps | buildtool-index | perception-models | bazel-repo-cache)
        "publish_${name//-/_}"
        ;;
      *)
        echo "unknown: ${name}" >&2
        exit 1
        ;;
    esac
  done
}

main "$@"
