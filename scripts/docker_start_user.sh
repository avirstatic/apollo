#!/usr/bin/env bash

###############################################################################
# Copyright 2020 The Apollo Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
###############################################################################

function _create_user_account() {
  local user_name="$1"
  local uid="$2"
  local group_name="$3"
  local gid="$4"
  addgroup --gid "${gid}" "${group_name}"

  adduser --disabled-password --force-badname --gecos '' \
    "${user_name}" --uid "${uid}" --gid "${gid}" # 2>/dev/null

  # copy init scripts manually
  cp /etc/skel/.* $(getent passwd ${user_name} | cut -d: -f6)/

  usermod -aG sudo "${user_name}"
  usermod -aG video "${user_name}"
}

function setup_user_bashrc() {
  local uid="$1"
  local gid="$2"
  local user_home="/home/$3"
  # cp -rf /etc/skel/.{profile,bash*} "${user_home}"
  local RCFILES_DIR="/opt/apollo/rcfiles"
  local rc
  if [[ -d "${RCFILES_DIR}" ]]; then
    for entry in ${RCFILES_DIR}/*; do
      rc=$(basename "${entry}")
      if [[ "${rc}" = user.* ]]; then
        cp -rf "${entry}" "${user_home}/${rc##user}"
      fi
    done
  fi
  # Set user files ownership to current user, such as .bashrc, .profile, etc.
  # chown -R "${uid}:${gid}" "${user_home}"
  chown -R "${uid}:${gid}" ${user_home}/.*
}

function setup_user_account_if_not_exist() {
  local user_name="$1"
  local uid="$2"
  local group_name="$3"
  local gid="$4"
  if grep -q "^${user_name}:" /etc/passwd; then
    echo "User ${user_name} already exist. Skip setting user account."
    return
  fi
  _create_user_account "$@"
  setup_user_bashrc "${uid}" "${gid}" "${user_name}"
}

function grant_device_permissions() {
  # setup GPS device
  [ -e /dev/novatel0 ] && chmod a+rw /dev/novatel0
  [ -e /dev/novatel1 ] && chmod a+rw /dev/novatel1
  [ -e /dev/novatel2 ] && chmod a+rw /dev/novatel2

  [ -e /dev/ttyACM0 ] && chmod a+rw /dev/ttyACM0
  [ -e /dev/imu ] && chmod a+rw /dev/imu

  # setup camera device
  [ -e /dev/camera/obstacle ] && chmod a+rw /dev/camera/obstacle
  [ -e /dev/camera/trafficlights ] && chmod a+rw /dev/camera/trafficlights

  # setup audio device
  [ -e /dev/snd ] && usermod -a -G audio "$1"

  true
}

function setup_apollo_directories() {
  local apollo_dir="/opt/apollo"
  [[ -d "${apollo_dir}" ]] || mkdir -p "${apollo_dir}"
  # chown -R "${uid}:${gid}" "${apollo_dir}"
  chmod a+rw /opt /opt/apollo
}

# FIXME(infra): This will change core pattern on the host also,
# where the `/apollo` directory may not exist.
function setup_core_pattern() {
  if [[ -w /proc/sys/kernel/core_pattern ]]; then
    echo "/apollo/data/core/core_%e.%p" > /proc/sys/kernel/core_pattern
  fi
}

# LLVM 14 compiles all C/C++/CUDA (tools/wllvm/{CC,CXX,CUDA}) and links the
# bitcode (tools/wllvm/extract_all.py).
LLVM_VERSION="14.0.0"
LLVM_PACKAGE="clang+llvm-${LLVM_VERSION}-x86_64-linux-gnu-ubuntu-18.04.tar.xz"
LLVM_URL="https://github.com/llvm/llvm-project/releases/download/llvmorg-${LLVM_VERSION}/${LLVM_PACKAGE}"
LLVM_SHA256="61582215dafafb7b576ea30cc136be92c877ba1f1c31ddbbd372d6d65622fef5"
LLVM_ROOT="/opt/llvm-14"

function install_llvm() {
  local user_name="$1"
  # Downloaded once, as the user, into the git-ignored cache that dev_start.sh
  # keeps; each new container only extracts it.
  local cache_dir="/apollo/.cache/llvm"
  local package="${cache_dir}/${LLVM_PACKAGE}"
  if ! echo "${LLVM_SHA256}  ${package}" | sha256sum -c --status 2>/dev/null; then
    echo "Downloading ${LLVM_URL} ..."
    if ! su - "${user_name}" -c "mkdir -p '${cache_dir}' && \
      curl -fL --retry 3 -o '${package}.part' '${LLVM_URL}' && \
      mv '${package}.part' '${package}'"; then
      echo "Error: failed to download ${LLVM_URL}" >&2
      return 1
    fi
    if ! echo "${LLVM_SHA256}  ${package}" | sha256sum -c --status; then
      echo "Error: checksum mismatch for ${package}" >&2
      return 1
    fi
  fi
  rm -rf "${LLVM_ROOT}"
  mkdir -p "${LLVM_ROOT}"
  # Static libraries are not needed (about 0.9G).
  tar -xJf "${package}" -C "${LLVM_ROOT}" --strip-components=1 --exclude='*.a' \
    || return 1
  # libomp.so (-fopenmp/-lomp in tools/bazel.rc) for the tools Bazel builds and
  # runs, e.g. protoc.
  echo "${LLVM_ROOT}/lib" > /etc/ld.so.conf.d/llvm-14.conf
  ldconfig
}

# Only LLVM 14 compiles: `clang`/`clang++` resolve to it, and the image's
# Clang 10 fails if anything still calls it.
function disable_image_clang() {
  local tool
  for tool in clang clang++; do
    update-alternatives --install "/usr/bin/${tool}" "${tool}" "${LLVM_ROOT}/bin/${tool}" 140
    update-alternatives --set "${tool}" "${LLVM_ROOT}/bin/${tool}"
  done
  local clang10="/usr/lib/llvm-10/bin/clang"
  if [[ -e "${clang10}" && ! -e "${clang10}.disabled" ]]; then
    mv "${clang10}" "${clang10}.disabled"
    printf '#!/bin/sh\necho "Clang 10 is disabled; use %s" >&2\nexit 1\n' \
      "${LLVM_ROOT}/bin/clang" > "${clang10}"
    chmod +x "${clang10}"
  fi
}

function setup_wllvm_toolchain() {
  local user_name="$1"
  install_llvm "${user_name}" || return 1
  disable_image_clang
  # WLLVM is installed per user (~/.local/bin), as tools/wllvm/{CC,CXX} expect
  su - "${user_name}" -c "python3 -m pip install --user wllvm==1.3.1" || return 1
  # WLLVM_BC_STORE in tools/wllvm/{CC,CXX} and tools/bazel.rc, and the output
  # root of tools/wllvm/extract_all.py; both git-ignored
  su - "${user_name}" -c "mkdir -p /apollo/wllvm_bc /apollo/wllvm_linked"
}

##===================== Main ==============================##
function main() {
  local user_name="$1"
  local uid="$2"
  local group_name="$3"
  local gid="$4"

  if [ "${uid}" != "${gid}" ]; then
    echo "Warning: uid(${uid}) != gid(${gid}) found."
  fi
  if [ "${user_name}" != "${group_name}" ]; then
    echo "Warning: user_name(${user_name}) != group_name(${group_name}) found."
  fi
  setup_user_account_if_not_exist "$@"
  setup_apollo_directories "${uid}" "${gid}"
  grant_device_permissions "${user_name}"
  setup_core_pattern
  # dev_start.sh stops if the toolchain cannot be installed, e.g. offline.
  setup_wllvm_toolchain "${user_name}" || exit 1
}

main "${DOCKER_USER}" "${DOCKER_USER_ID}" "${DOCKER_GRP}" "${DOCKER_GRP_ID}"
