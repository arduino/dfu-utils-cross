# dfu-util

Static dfu-util builds for Linux, macOS and Windows, in the layout used by the
Arduino `dfu-util` tool.

The workflow clones upstream dfu-util at a pinned commit, applies the patches in
`patches/`, links it against a static libusb built from a pinned, checksummed
release tarball, and tests the Linux binaries against an emulated DFU device.
No dfu-util or libusb sources are kept in this repository.

Pushing a `v<PROJECT_VERSION>` tag (e.g. `v0.11-arduino6`) publishes a GitHub
release, once all builds and tests pass.

## Tests

`tests/run.py` attaches an emulated DFU 1.1 / DfuSe device to the kernel's
USB/IP virtual host controller (`vhci-hcd`) and runs dfu-util against it, using
the upload commands of the Arduino Renesas, mbed and Zephyr cores plus generic
download, upload, reset and detach cases:

    sudo modprobe vhci-hcd
    sudo python3 -I tests/run.py --dfu-util path/to/dfu-util

`--only NAME[,NAME...]` runs selected tests (`--list` shows them), `--verbose`
prints dfu-util's output and `--trace` every USB request the device received.

## Versions

Set in `.github/workflows/build.yml`:

- `PROJECT_VERSION` - release tag and the version reported by `dfu-util -V`.
- `DFU_UTIL_COMMIT` - upstream commit from https://git.code.sf.net/p/dfu-util/dfu-util.
- `LIBUSB_VERSION` / `LIBUSB_SHA256` - libusb release tarball.
- `LLVM_MINGW_VERSION` / `LLVM_MINGW_SHA256` - Windows cross toolchain.

## Patches

- `0001` - Print the missing DFU suffix warning only with `-v`.
- `0002` - Add `-Q`/`--final-detach`: send DFU_DETACH when finished, without a
  USB reset.

## Targets

Each release has one `dfu-util-<version>-<os>_<arch>.tar.gz` per target,
containing `dfu-util`, `dfu-suffix` and `dfu-prefix`:

- `linux_amd64`, `linux_386`, `linux_arm`, `linux_arm64` - fully static.
- `darwin_amd64` (macOS 10.13+), `darwin_arm64` (macOS 11.0+) - libusb linked
  statically, system frameworks only, ad-hoc signed.
- `windows_386`, `windows_amd64`, `windows_arm64` - fully static, UCRT
  (Windows 10+).
