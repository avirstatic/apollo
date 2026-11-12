/******************************************************************************
 * Copyright 2020 The Apollo Authors. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the License);
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an AS IS BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *****************************************************************************/
#if defined(__clang__) && defined(__CUDA__)
// Clang rejects Eigen 3.3.7's out-of-line definitions that omit
// EIGEN_DEVICE_FUNC. The kernels here do not use Eigen, so include it
// host-only in both compilation passes; nvcc is unaffected.
#pragma push_macro("__CUDACC__")
#pragma push_macro("__CUDA_ARCH__")
#undef __CUDACC__
#undef __CUDA_ARCH__
#include "Eigen/Core"
#pragma pop_macro("__CUDA_ARCH__")
#pragma pop_macro("__CUDACC__")
#endif

#include <algorithm>
#include <functional>
#include <map>
#include <memory>
#include <utility>
#include <vector>
#include "boost/iterator/counting_iterator.hpp"
#include "thrust/functional.h"
#include "thrust/sort.h"

#include "modules/perception/base/object_types.h"
#include "modules/perception/camera/lib/obstacle/detector/smoke/object_maintainer.h"
#include "modules/perception/camera/lib/obstacle/detector/smoke/region_output.h"

namespace apollo {
namespace perception {
namespace camera {

int get_smoke_objects_gpu() {
  int num_kept = 0;

  return num_kept;
}


}  // namespace camera
}  // namespace perception
}  // namespace apollo
