#! /usr/bin/env bash

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
###############################################################################

set -e

TOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "${TOP_DIR}/scripts/apollo_base.sh"

function extract_linked_ir() {
  # CPU builds and --config=cuda_ir compile every C/C++ action through WLLVM
  # (tools/wllvm), so a full build can be linked into whole-program IR per
  # cc_binary and cc_test. wllvm_linked holds only the latest build's IR.
  if [[ "${USE_GPU}" -eq 1 && "${CMDLINE_OPTIONS}" != *"--config=cuda_ir"* ]]; then
    warning "GPU build without --config=cuda_ir uses nvcc/gcc, not WLLVM; skip" \
      "LLVM IR linking. Use './apollo.sh build_gpu' for bitcode."
    return
  fi
  local output="${APOLLO_ROOT_DIR}/wllvm_linked"
  rm -rf "${output:?}"/*
  local libdevice="/usr/local/cuda/nvvm/libdevice/libdevice.10.bc"
  local opts=()
  if [ "${USE_GPU}" -eq 1 ] && [ -f "${libdevice}" ]; then
    opts+=(--libdevice "${libdevice}")
  fi
  info "Linking LLVM IR for each cc_binary and cc_test into ${output} ..."
  if python3 "${APOLLO_ROOT_DIR}/tools/wllvm/extract_all.py" \
      --output "${output}" "${opts[@]}"; then
    ok "Linked LLVM IR written to ${output}"
  else
    error "LLVM IR linking failed for some targets; see ${output}/index.json"
    exit 1
  fi
}

function main() {
  site_restore
  parse_cmdline_arguments "$@"
  run_bazel "Build"
  if [ -z "${SHORTHAND_TARGETS}" ]; then
    SHORTHAND_TARGETS="apollo"
    extract_linked_ir
  fi
  success "Done building ${SHORTHAND_TARGETS}. Enjoy!"
}

main "$@"
