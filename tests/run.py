"""Run dfu-util against an emulated USB/IP DFU device.

Usage (as root, with vhci-hcd loaded):
  run.py --dfu-util PATH [--only NAME[,NAME...]] [--verbose] [--trace]
  run.py --list

Each scenario attaches a fresh emulated device, runs dfu-util once, then
checks the exit code, the output and the device's final state. Exits with
status 1 if any scenario fails.
"""

import argparse
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usbip_dfu import (DFU_ERROR, DNLOAD_IDLE, DfuDevice,  # noqa: E402
                       count_usb_devices, wait_until)

SUFFIX_WARNING = "A valid DFU suffix will be required"

COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_RESET = "\033[0m"

rng = random.Random(0x0DF0)
FIRMWARE = rng.randbytes(5000)
IMAGE = rng.randbytes(3000)
DFUSE_IMAGE = rng.randbytes(8192)
SKETCH = rng.randbytes(256 * 1024)
LARGE_SKETCH = rng.randbytes(1536 * 1024)

# Emulated devices. USB IDs are the bootloader (DFU mode) IDs from the
# Arduino cores' boards.txt, so the commands below run unchanged. Flash
# layouts are representative, not exact.
GENERIC = {}
H7 = {"vid": 0x2341, "pid_dfu": 0x035B, "dfuse": True,
      "dfuse_page": 128 * 1024, "dfuse_pages": 16}
GIGA = dict(H7, pid_dfu=0x0366)
NICLA_VISION = dict(H7, pid_dfu=0x035F)
OPTA = dict(H7, pid_dfu=0x0364)
C33 = {"vid": 0x2341, "pid_runtime": 0x0068, "pid_dfu": 0x0368, "alts": 2,
       "on_detach": "disconnect"}
UNO_R4_MINIMA = {"vid": 0x2341, "pid_runtime": 0x0069, "pid_dfu": 0x0369,
                 "on_detach": "disconnect"}


# Checks. Each is (label, function of the result).

def exits(code):
    return ("exit code %d" % code, lambda r: r["exit"] == code)


def fails():
    return ("non-zero exit code", lambda r: r["exit"] > 0)


def output_has(text):
    return ("output has '%s'" % text, lambda r: text in r["output"])


def output_lacks(text):
    return ("output lacks '%s'" % text, lambda r: text not in r["output"])


def event(name):
    return ("device saw %s" % name, lambda r: name in r["events"])


def no_event(name):
    return ("device did not see %s" % name, lambda r: name not in r["events"])


def no_event_like(prefix):
    return ("device saw no %s*" % prefix,
            lambda r: not any(e.startswith(prefix) for e in r["events"]))


def memory_at(offset, data):
    return ("device memory at +0x%x matches firmware" % offset,
            lambda r: r["memory"][offset:offset + len(data)] == data)


def memory_not_at(offset, data):
    return ("device memory at +0x%x does not hold the full firmware" % offset,
            lambda r: r["memory"][offset:offset + len(data)] != data)


def upload_is(data):
    return ("uploaded file matches device", lambda r: r["upload"] == data)


# dfu-util command lines, as the Arduino cores' platform.txt build them.

def renesas(pid_runtime, pid_dfu):
    return ["--device", "0x2341:%s,:%s" % (pid_runtime, pid_dfu), "-D", "{fw}", "-a0", "-Q"]


def mbed(pid, address):
    return ["--device", "0x2341:%s" % pid, "-D", "{fw}", "-a0", "--dfuse-address=%s" % address]


def zephyr(pid, address):
    return ["--device", ",0x2341:%s" % pid, "-D", "{fw}", "-a0", "--dfuse-address=%s" % address]


ID = "1209:0002"
DOWNLOAD = ["-d", ID, "-a", "0", "-D", "{fw}"]
DFUSE_DOWNLOAD = ["-d", ID, "-a", "0", "-s", "0x08000000", "-D", "{fw}"]


class Scenario(NamedTuple):
    name: str
    device: dict
    firmware: bytes
    args: list
    checks: list
    count: int = 1             # number of identical devices attached
    attach_delay: float = 0    # attach the devices this long after dfu-util started


SCENARIOS = [
    # Generic behavior.
    Scenario("list", GENERIC, FIRMWARE, ["-l"],
             [exits(0), output_has("Found DFU: [1209:0002]")]),
    Scenario("download", GENERIC, FIRMWARE, DOWNLOAD,
             [exits(0), memory_at(0, FIRMWARE), event("manifest"), output_lacks(SUFFIX_WARNING)]),
    Scenario("download-verbose", GENERIC, FIRMWARE, ["-v"] + DOWNLOAD,
             [exits(0), memory_at(0, FIRMWARE), output_has(SUFFIX_WARNING)]),
    Scenario("upload", dict(image=IMAGE), FIRMWARE, ["-d", ID, "-a", "0", "-U", "{out}"],
             [exits(0), upload_is(IMAGE)]),
    Scenario("download-reset-short", GENERIC, FIRMWARE, DOWNLOAD + ["-R"],
             [exits(0), memory_at(0, FIRMWARE), event("detach"), event("reset")]),
    Scenario("download-reset-long", GENERIC, FIRMWARE, DOWNLOAD + ["--reset"],
             [exits(0), memory_at(0, FIRMWARE), event("detach"), event("reset")]),
    Scenario("download-reset-first", GENERIC, FIRMWARE, ["-R"] + DOWNLOAD,
             [exits(0), memory_at(0, FIRMWARE), event("detach"), event("reset")]),
    Scenario("download-final-detach", GENERIC, FIRMWARE, DOWNLOAD + ["-Q"],
             [exits(0), memory_at(0, FIRMWARE), event("detach"), no_event("reset")]),
    Scenario("download-final-detach-leaves", dict(on_detach="disconnect"), FIRMWARE,
             DOWNLOAD + ["-Q"],
             [exits(0), memory_at(0, FIRMWARE), event("detach"), no_event("reset")]),
    Scenario("download-not-tolerant", dict(manifest_tolerant=False), FIRMWARE, DOWNLOAD,
             [exits(0), memory_at(0, FIRMWARE), event("reset")]),
    Scenario("dfuse-download", dict(dfuse=True), FIRMWARE, DFUSE_DOWNLOAD,
             [exits(0), memory_at(0, FIRMWARE), event("erase:0x08000000"),
              event("erase:0x08001000"), no_event("leave")]),
    Scenario("dfuse-download-leave", dict(dfuse=True), FIRMWARE,
             ["-d", ID, "-a", "0", "-s", "0x08000000:leave", "-D", "{fw}"],
             [exits(0), memory_at(0, FIRMWARE), event("leave"), event("disconnect")]),
    Scenario("dfuse-upload", dict(dfuse=True, image=DFUSE_IMAGE), FIRMWARE,
             ["-d", ID, "-a", "0", "-s", "0x08000000:4096", "-U", "{out}"],
             [exits(0), upload_is(DFUSE_IMAGE[:4096])]),
    Scenario("runtime-will-detach", dict(runtime=True, will_detach=True), FIRMWARE,
             ["-d", "1209:0001,1209:0002", "-E", "1", "-a", "0", "-D", "{fw}"],
             [exits(0), memory_at(0, FIRMWARE), event("detach"), no_event("reset")]),
    Scenario("runtime-detach-reset", dict(runtime=True), FIRMWARE,
             ["-d", "1209:0001,1209:0002", "-E", "1", "-a", "0", "-D", "{fw}"],
             [exits(0), memory_at(0, FIRMWARE), event("detach"), event("reset")]),

    # Arduino Renesas core (platform.txt tools.dfu-util.upload.pattern).
    Scenario("arduino-renesas-c33-from-sketch", dict(C33, runtime=True, will_detach=True),
             SKETCH, renesas("0x0068", "0x0368"),
             [exits(0), memory_at(0, SKETCH), event("detach"), event("disconnect")]),
    Scenario("arduino-renesas-c33-in-bootloader", C33, SKETCH, renesas("0x0068", "0x0368"),
             [exits(0), memory_at(0, SKETCH), event("detach"), event("disconnect")]),
    Scenario("arduino-renesas-uno-r4-minima-from-sketch",
             dict(UNO_R4_MINIMA, runtime=True, will_detach=True), SKETCH,
             renesas("0x0069", "0x0369"),
             [exits(0), memory_at(0, SKETCH), event("detach"), event("disconnect")]),
    Scenario("arduino-renesas-uno-r4-minima-in-bootloader", UNO_R4_MINIMA, SKETCH,
             renesas("0x0069", "0x0369"),
             [exits(0), memory_at(0, SKETCH), event("detach"), event("disconnect")]),

    # Arduino mbed cores.
    Scenario("arduino-mbed-portenta-h7", H7, SKETCH, mbed("0x035b", "0x08040000:leave"),
             [exits(0), memory_at(0x40000, SKETCH), event("erase:0x08040000"), event("leave")]),
    Scenario("arduino-mbed-giga", GIGA, SKETCH, mbed("0x0366", "0x08040000:leave"),
             [exits(0), memory_at(0x40000, SKETCH), event("erase:0x08040000"), event("leave")]),

    # Arduino Zephyr core (sketch.cmd and loader.cmd).
    Scenario("arduino-zephyr-portenta-h7-sketch", H7, SKETCH,
             zephyr("0x035b", "0x080e0000:leave"),
             [exits(0), memory_at(0xE0000, SKETCH), event("leave")]),
    Scenario("arduino-zephyr-portenta-h7-loader", H7, SKETCH, zephyr("0x035b", "0x08040000"),
             [exits(0), memory_at(0x40000, SKETCH), no_event("leave")]),
    Scenario("arduino-zephyr-giga-sketch", GIGA, SKETCH, zephyr("0x0366", "0x080e0000:leave"),
             [exits(0), memory_at(0xE0000, SKETCH), event("leave")]),
    Scenario("arduino-zephyr-portenta-c33-sketch", C33, SKETCH,
             ["--device", ",0x2341:0x0368", "-D", "{fw}", "-a1", "-w", "-Q"],
             [exits(0), memory_at(0, SKETCH), event("alt:1"), event("detach")],
             attach_delay=1.0),
    Scenario("arduino-zephyr-portenta-c33-loader", C33, SKETCH,
             ["--device", ",0x2341:0x0368", "-D", "{fw}", "-a0"],
             [exits(0), memory_at(0, SKETCH), event("alt:0"), no_event("detach")]),
    Scenario("arduino-zephyr-nicla-vision-sketch", NICLA_VISION, SKETCH,
             zephyr("0x035f", "0x080e0000:leave"),
             [exits(0), memory_at(0xE0000, SKETCH), event("leave")]),
    Scenario("arduino-zephyr-opta-sketch", OPTA, SKETCH, zephyr("0x0364", "0x080e0000:leave"),
             [exits(0), memory_at(0xE0000, SKETCH), event("leave")]),

    # Arduino Zephyr core, "Burn Bootloader" (flash-loader-only: loader.cmd + leave.dfuse).
    Scenario("arduino-zephyr-portenta-h7-burn-bootloader", H7, SKETCH,
             zephyr("0x035b", "0x08040000:leave"),
             [exits(0), memory_at(0x40000, SKETCH), event("leave")]),
    Scenario("arduino-zephyr-giga-burn-bootloader", GIGA, SKETCH,
             zephyr("0x0366", "0x08040000:leave"),
             [exits(0), memory_at(0x40000, SKETCH), event("leave")]),
    Scenario("arduino-zephyr-portenta-c33-burn-bootloader", C33, SKETCH,
             ["--device", ",0x2341:0x0368", "-D", "{fw}", "-a0", "-Q"],
             [exits(0), memory_at(0, SKETCH), event("alt:0"), event("detach")]),

    # Failures and leftovers an IDE upload can run into.
    # A plain "-d VID:PID" only filters run-time devices; DFU-mode devices are
    # matched by the part after the comma, which defaults to any.
    Scenario("no-matching-device", GENERIC, FIRMWARE,
             ["-d", ",1209:00ff", "-a", "0", "-D", "{fw}"],
             [exits(74), output_has("No DFU capable USB device available"),
              no_event("manifest")]),
    Scenario("two-matching-devices", GENERIC, FIRMWARE, DOWNLOAD,
             [exits(74), output_has("More than one DFU capable USB device found"),
              no_event("manifest")],
             count=2),
    Scenario("write-error", dict(fail_on_block=2), FIRMWARE, DOWNLOAD,
             [fails(), event("write-error"), memory_not_at(0, FIRMWARE), no_event("manifest")]),
    Scenario("dfuse-write-error", dict(H7, fail_on_block=10), SKETCH,
             mbed("0x035b", "0x08040000:leave"),
             [fails(), event("write-error"), memory_not_at(0x40000, SKETCH), no_event("leave")]),
    Scenario("dfuse-image-too-large", H7, SKETCH, mbed("0x035b", "0x081f0000:leave"),
             [exits(64), output_has("is not writeable"), no_event_like("erase:"),
              no_event("leave")]),
    Scenario("start-in-dfu-error", dict(initial_state=DFU_ERROR), FIRMWARE, DOWNLOAD,
             [exits(0), event("clrstatus"), memory_at(0, FIRMWARE)]),
    Scenario("start-in-dnload-idle", dict(initial_state=DNLOAD_IDLE), FIRMWARE, DOWNLOAD,
             [exits(0), event("abort"), memory_at(0, FIRMWARE)]),
    Scenario("large-sketch-portenta-h7", H7, LARGE_SKETCH, mbed("0x035b", "0x08040000:leave"),
             [exits(0), memory_at(0x40000, LARGE_SKETCH), event("leave")]),
]


def run_scenario(dfu_util, scenario):
    with tempfile.TemporaryDirectory() as tmp:
        fw = Path(tmp) / "fw.bin"
        out = Path(tmp) / "out.bin"
        fw.write_bytes(scenario.firmware)
        argv = [dfu_util] + [a.format(fw=fw, out=out) for a in scenario.args]

        devices = [DfuDevice(**scenario.device, devid=0x10001 + i)
                   for i in range(scenario.count)]
        device = devices[0]

        def plug_in():
            for d in devices:
                d.attach()
            wait_until(lambda: count_usb_devices(device.vid, device.pid) >= len(devices),
                       "the emulated device to enumerate")

        if not scenario.attach_delay:
            plug_in()
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if scenario.attach_delay:
            time.sleep(scenario.attach_delay)
            plug_in()
        try:
            output, _ = proc.communicate(timeout=60)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate()
            output = "TIMEOUT\n" + output
            code = -1
        time.sleep(0.3)
        for d in devices:
            d.stop()
        # Wait until the devices are gone, so the next scenario cannot find
        # one that is still being removed.
        wait_until(lambda: not any(count_usb_devices(device.vid, pid)
                                   for pid in (device.pid_runtime, device.pid_dfu)),
                   "the emulated device to be removed")
        upload = out.read_bytes() if out.exists() else None

    result = {
        "argv": " ".join(str(a) for a in argv[1:]).replace(tmp + "/", ""),
        "exit": code,
        "output": output.replace(tmp + "/", ""),
        "events": list(device.events),
        "trace": list(device.trace),
        "memory": bytes(device.memory),
        "upload": upload,
    }
    result["failed"] = [label for label, fn in scenario.checks if not fn(result)]
    return result


def print_result(name, result, time_ms):
    s = "Test (%s)" % name
    padding = "." * max(60 - len(s), 3)
    if result["failed"]:
        status = COLOR_RED + "FAILED" + COLOR_RESET
    else:
        status = COLOR_GREEN + "PASSED" + COLOR_RESET
    print(s + padding + status + " (%dms)" % time_ms, flush=True)


def print_details(result, trace):
    for label in result["failed"]:
        print(COLOR_RED + "  failed: %s" % label + COLOR_RESET)
    print("  $ dfu-util %s" % result["argv"])
    print("  exit %d, events: %s" % (result["exit"], ", ".join(result["events"])))
    for line in result["output"].replace("\r", "\n").splitlines():
        if line.strip():
            print("  | %s" % line)
    if trace:
        for line in result["trace"]:
            print("  > %s" % line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dfu-util")
    parser.add_argument("--only", help="comma-separated scenario names")
    parser.add_argument("--list", action="store_true", help="list scenarios")
    parser.add_argument("--verbose", action="store_true",
                        help="print dfu-util's command line, output and device events")
    parser.add_argument("--trace", action="store_true",
                        help="print every USB request the device received")
    args = parser.parse_args()

    if args.list:
        for s in SCENARIOS:
            print(s.name)
        return 0
    if not args.dfu_util:
        parser.error("--dfu-util is required")

    only = set(args.only.split(",")) if args.only else None
    version = subprocess.run([args.dfu_util, "-V"], capture_output=True,
                             text=True).stdout.splitlines()[0]
    print("Testing %s (%s)\n" % (args.dfu_util, version))

    passed = failed = 0
    total_start = time.monotonic()
    for scenario in SCENARIOS:
        if only and scenario.name not in only:
            continue
        start = time.monotonic()
        result = run_scenario(args.dfu_util, scenario)
        print_result(scenario.name, result, (time.monotonic() - start) * 1000)
        if result["failed"]:
            failed += 1
        else:
            passed += 1
        if args.verbose or args.trace or result["failed"]:
            print_details(result, args.trace)

    total_ms = (time.monotonic() - total_start) * 1000
    stats = "Total: %d | Passed: %d | Failed: %d | Time: %dms" % (
        passed + failed, passed, failed, total_ms)
    separator = "=" * len(stats)
    print("\n" + separator)
    print("Total: %d | " % (passed + failed) +
          COLOR_GREEN + "Passed: %d" % passed + COLOR_RESET + " | " +
          COLOR_RED + "Failed: %d" % failed + COLOR_RESET + " | " +
          "Time: %dms" % total_ms)
    print(separator)
    if failed:
        print(COLOR_RED + "Some tests FAILED." + COLOR_RESET)
    else:
        print(COLOR_GREEN + "All tests PASSED." + COLOR_RESET)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
