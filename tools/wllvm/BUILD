load("@rules_cc//cc:defs.bzl", "cc_toolchain", "cc_toolchain_suite")

package(default_visibility = ["//visibility:public"])

filegroup(
    name = "empty",
    srcs = [],
)

filegroup(
    name = "wllvm_wrapper",
    srcs = [
        "CC",
        "CXX",
    ],
)

filegroup(
    name = "compiler_files",
    srcs = [
        ":wllvm_wrapper",
        "@local_config_cc//:compiler_deps",
    ],
)

cc_toolchain_suite(
    name = "toolchain",
    toolchains = {
        "k8": ":cc-compiler-clang",
        "k8|clang": ":cc-compiler-clang",
        "k8|compiler": ":cc-compiler-clang",
        "local": ":cc-compiler-clang",
        "local|clang": ":cc-compiler-clang",
        "local|compiler": ":cc-compiler-clang",
    },
)

cc_toolchain(
    name = "cc-compiler-clang",
    all_files = ":compiler_files",
    compiler_files = ":compiler_files",
    dwp_files = ":empty",
    linker_files = ":compiler_files",
    objcopy_files = ":empty",
    strip_files = ":empty",
    supports_param_files = 0,
    toolchain_identifier = "local-clang",
    toolchain_config = "@local_config_cc//:local",
)