"""Emulated DFU device, attached to the local vhci_hcd over USB/IP.

The device side of USB/IP runs in this process: one end of a stream
socket pair is handed to vhci_hcd through its sysfs attach file, and the
kernel then sends CMD_SUBMIT/CMD_UNLINK requests for the device on it.
Needs root and the vhci-hcd module.

Kernel behavior relied on (drivers/usb/usbip, v6.8):
- attach takes "<port> <sockfd> <devid> <speed>", detach takes "<port>".
- SET_ADDRESS and root hub port resets are handled by vhci_hcd and never
  reach the device. A bus reset is seen here as a new device descriptor
  request after the device was configured.
- Headers are 48 bytes in network byte order. OUT data follows CMD_SUBMIT,
  IN data follows RET_SUBMIT.
"""

import socket
import struct
import threading
import time
from contextlib import suppress
from pathlib import Path

VHCI = Path("/sys/devices/platform/vhci_hcd.0")

USBIP_CMD_SUBMIT = 1
USBIP_CMD_UNLINK = 2
USBIP_RET_SUBMIT = 3
USBIP_RET_UNLINK = 4
USBIP_DIR_IN = 1
USB_SPEED_HIGH = 3
VDEV_ST_NULL = 4
EPIPE = 32

BASIC = struct.Struct(">IIIII")
CMD_SUBMIT = struct.Struct(">Iiiii8s")
RET_SUBMIT = struct.Struct(">iiiii8x")
RET_UNLINK = struct.Struct(">i24x")
HEADER_SIZE = BASIC.size + CMD_SUBMIT.size
SETUP = struct.Struct("<BBHHH")

# Standard requests, request types and descriptor types (USB 2.0).
GET_STATUS, CLEAR_FEATURE, SET_FEATURE = 0, 1, 3
GET_DESCRIPTOR, GET_CONFIGURATION, SET_CONFIGURATION, SET_INTERFACE = 6, 8, 9, 11
REQ_TYPE_STANDARD, REQ_TYPE_CLASS = 0, 1
RECIP_INTERFACE = 1
DT_DEVICE, DT_CONFIG, DT_STRING, DT_INTERFACE, DT_DFU_FUNCTIONAL = 1, 2, 3, 4, 0x21

STANDARD_REQUESTS = {GET_STATUS: "GET_STATUS", CLEAR_FEATURE: "CLEAR_FEATURE",
                     SET_FEATURE: "SET_FEATURE", GET_DESCRIPTOR: "GET_DESCRIPTOR",
                     GET_CONFIGURATION: "GET_CONFIGURATION",
                     SET_CONFIGURATION: "SET_CONFIGURATION", SET_INTERFACE: "SET_INTERFACE"}

# DFU requests, states and status codes (DFU 1.1).
DFU_DETACH, DFU_DNLOAD, DFU_UPLOAD, DFU_GETSTATUS = 0, 1, 2, 3
DFU_CLRSTATUS, DFU_GETSTATE, DFU_ABORT = 4, 5, 6
DFU_REQUESTS = ["DETACH", "DNLOAD", "UPLOAD", "GETSTATUS", "CLRSTATUS", "GETSTATE", "ABORT"]

APP_IDLE, APP_DETACH = 0, 1
DFU_IDLE, DNLOAD_SYNC, DNBUSY, DNLOAD_IDLE = 2, 3, 4, 5
MANIFEST_SYNC, MANIFEST, MANIFEST_WAIT_RESET = 6, 7, 8
UPLOAD_IDLE, DFU_ERROR = 9, 10

STATUS_OK = 0x00
STATUS_ERR_WRITE = 0x03
STATUS_ERR_ADDRESS = 0x08
STATUS_ERR_UNKNOWN = 0x0E
STATUS_ERR_STALLEDPKT = 0x0F

ATTR_CAN_DNLOAD = 1 << 0
ATTR_CAN_UPLOAD = 1 << 1
ATTR_MANIFEST_TOL = 1 << 2
ATTR_WILL_DETACH = 1 << 3


class Stall(Exception):
    pass


# Control requests

def parse_setup(setup):
    """Returns (type, recipient, bRequest, wValue, wIndex, wLength)."""
    req_type, request, value, index, length = SETUP.unpack(setup)
    return (req_type >> 5) & 3, req_type & 0x1F, request, value, index, length


def describe(setup):
    kind, _, request, value, index, length = parse_setup(setup)
    if kind == REQ_TYPE_CLASS:
        name = DFU_REQUESTS[request] if request < len(DFU_REQUESTS) else "class %d" % request
    else:
        name = STANDARD_REQUESTS.get(request, "request %d" % request)
    return "%-17s wValue=0x%04x wIndex=%d wLength=%d" % (name, value, index, length)


def split_result(result):
    """DfuDevice.control() returns bytes, None, an action, or (bytes, action).
    Returns (data, action)."""
    if isinstance(result, tuple):
        return result
    if isinstance(result, str):
        return None, result
    return result, None


# Descriptors

def device_descriptor(vid, pid):
    return struct.pack("<BBHBBBBHHHBBBB", 18, DT_DEVICE, 0x0200, 0, 0, 0, 64,
                       vid, pid, 0x0100, 1, 2, 3, 1)


def config_descriptor(body):
    """One configuration with one interface, followed by its descriptors."""
    return struct.pack("<BBHBBBBB", 9, DT_CONFIG, 9 + len(body), 1, 1, 0, 0x80, 50) + body


def interface_descriptor(alt, protocol, name_index):
    """DFU interface (class 0xFE, subclass 1), one per alternate setting."""
    return struct.pack("<BBBBBBBBB", 9, DT_INTERFACE, 0, alt, 0, 0xFE, 1, protocol, name_index)


def dfu_functional_descriptor(attrs, transfer_size, version):
    return struct.pack("<BBBHHH", 9, DT_DFU_FUNCTIONAL, attrs, 1000, transfer_size, version)


def string_descriptor(text):
    data = text.encode("utf-16-le")
    return bytes([2 + len(data), DT_STRING]) + data


# USB/IP messages

def recv_exact(f, n):
    buf = f.read(n)
    if len(buf) < n:
        raise ConnectionError()
    return buf


def ret_submit(seqnum, devid, direction, ep, status, actual, payload):
    return (BASIC.pack(USBIP_RET_SUBMIT, seqnum, devid, direction, ep) +
            RET_SUBMIT.pack(status, actual, 0, 0, 0) + payload)


def ret_unlink(seqnum, devid):
    # Status 0 means the request already completed, which is what usbip-host
    # replies in that case, and every request here completes immediately.
    return BASIC.pack(USBIP_RET_UNLINK, seqnum, devid, 0, 0) + RET_UNLINK.pack(0)


# vhci_hcd and sysfs

def free_port():
    for line in (VHCI / "status").read_text().splitlines()[1:]:
        fields = line.split()
        if fields[0] == "hs" and int(fields[2]) == VDEV_ST_NULL:
            return int(fields[1])
    raise RuntimeError("no free vhci_hcd high-speed port")


def count_usb_devices(vid, pid):
    """Number of configured USB devices with these IDs."""
    count = 0
    for d in Path("/sys/bus/usb/devices").iterdir():
        try:
            if (int((d / "idVendor").read_text(), 16) == vid and
                    int((d / "idProduct").read_text(), 16) == pid and
                    (d / "bConfigurationValue").read_text().strip()):
                count += 1
        except (OSError, ValueError):
            # The device went away while it was being read (ENOENT, ENODEV).
            continue
    return count


def wait_until(condition, what, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise TimeoutError("timed out waiting for %s" % what)


class DfuDevice:
    """An emulated DFU 1.1 or DfuSe device, plugged into vhci_hcd over USB/IP.

    attach() plugs it in and starts a thread that answers the kernel's
    requests, detach() unplugs it. The state (memory, events, trace) stays
    on the object across attachments and after stop().

    vid, pid_runtime, pid_dfu: USB IDs in run-time and DFU mode.
    alts: number of alternate settings of the DFU interface.
    runtime: start in run-time mode (bInterfaceProtocol 1) and switch to
        DFU mode on DETACH (with will_detach) or on the following bus reset.
    dfuse: behave as an ST DfuSe device (bcdDFUVersion 0x011a), with flash
        at dfuse_base made of dfuse_pages pages of dfuse_page bytes.
    on_reset: what a bus reset does in DFU mode: "disconnect" (the device
        boots its application and leaves) or "stay".
    on_detach: what DFU_DETACH does in DFU mode: "disconnect" or "stay".
    fail_on_block: fail the Nth data block (0-based) with errWRITE.
    initial_state: start in this DFU state (e.g. DFU_ERROR or DNLOAD_IDLE),
        as left behind by an interrupted upload.
    devid: USB/IP device ID, unique per device attached at the same time.
    """

    def __init__(self, vid=0x1209, pid_runtime=0x0001, pid_dfu=0x0002, alts=1,
                 runtime=False, dfuse=False, dfuse_base=0x08000000,
                 dfuse_page=1024, dfuse_pages=8, manifest_tolerant=True,
                 will_detach=False, on_reset="disconnect", on_detach="stay",
                 transfer_size=1024, image=b"", fail_on_block=None,
                 initial_state=None, devid=0x10001):
        self.vid = vid
        self.pid_runtime = pid_runtime
        self.pid_dfu = pid_dfu
        self.alts = alts
        self.runtime = runtime
        self.dfuse = dfuse
        self.dfuse_base = dfuse_base
        self.dfuse_page = dfuse_page
        self.dfuse_pages = dfuse_pages
        self.manifest_tolerant = manifest_tolerant
        self.will_detach = will_detach
        self.on_reset = on_reset
        self.on_detach = on_detach
        self.transfer_size = transfer_size
        self.fail_on_block = fail_on_block
        if dfuse:
            self.memory = bytearray(b"\xff" * (dfuse_page * dfuse_pages))
            self.memory[:len(image)] = image
        else:
            self.memory = bytearray(image)
        self.events = []
        self.trace = []
        self.lock = threading.RLock()
        self.devid = devid
        self.port = None
        self.sock = None
        self.thread = None
        self.attached = False
        self.t0 = time.monotonic()
        self._reset_state()
        if initial_state is not None:
            self.state = initial_state
            if initial_state == DFU_ERROR:
                self.status = STATUS_ERR_UNKNOWN

    def _reset_state(self):
        self.state = APP_IDLE if self.runtime else DFU_IDLE
        self.status = STATUS_OK
        self.configured = False
        self.alt = 0
        self.dnload = bytearray()
        self.upload_offset = 0
        self.address = self.dfuse_base
        self.leaving = False
        self.blocks = 0
        self.write_error = False

    def enter_dfu_mode(self):
        self.runtime = False
        self._reset_state()

    def event(self, name):
        with self.lock:
            self.events.append(name)

    # Descriptors

    @property
    def pid(self):
        return self.pid_runtime if self.runtime else self.pid_dfu

    def alt_names(self):
        if self.runtime:
            return ["Runtime"]
        if self.dfuse:
            layout = "@Internal Flash  /0x%08x/%02d*%03dKg" % (
                self.dfuse_base, self.dfuse_pages, self.dfuse_page // 1024)
            return [layout] * self.alts
        return ["Flash %d" % alt for alt in range(self.alts)]

    def configuration(self):
        attrs = ATTR_CAN_DNLOAD | ATTR_CAN_UPLOAD
        if self.manifest_tolerant:
            attrs |= ATTR_MANIFEST_TOL
        if self.will_detach:
            attrs |= ATTR_WILL_DETACH
        version = 0x011A if (self.dfuse and not self.runtime) else 0x0110
        protocol = 1 if self.runtime else 2
        body = b"".join(interface_descriptor(alt, protocol, 4 + alt)
                        for alt in range(len(self.alt_names())))
        body += dfu_functional_descriptor(attrs, self.transfer_size, version)
        return config_descriptor(body)

    def string(self, index):
        strings = ["dfu-util-test", "Emulated DFU device", "0001"] + self.alt_names()
        if index == 0:
            return bytes([4, DT_STRING, 0x09, 0x04])
        if 1 <= index <= len(strings):
            return string_descriptor(strings[index - 1])
        raise Stall()

    # Control requests. Return the IN data (None for OUT requests), or a
    # (data, action) tuple, or an action string. Actions: "reset" (bus reset
    # seen), "leave" (drop off the bus), "switch" (re-enumerate in DFU mode).

    def control(self, setup, data):
        kind, recipient, request, value, _, length = parse_setup(setup)
        if kind == REQ_TYPE_STANDARD:
            return self.standard(request, value)
        if kind == REQ_TYPE_CLASS and recipient == RECIP_INTERFACE:
            return self.dfu(request, value, length, data)
        raise Stall()

    def standard(self, request, value):
        if request == GET_DESCRIPTOR:
            kind, index = value >> 8, value & 0xFF
            if kind == DT_DEVICE:
                if self.configured:
                    return "reset"
                return device_descriptor(self.vid, self.pid)
            if kind == DT_CONFIG:
                return self.configuration()
            if kind == DT_STRING:
                return self.string(index)
            raise Stall()
        if request == SET_CONFIGURATION:
            self.configured = True
            return None
        if request == SET_INTERFACE:
            if value >= len(self.alt_names()):
                raise Stall()
            self.alt = value
            self.event("alt:%d" % value)
            return None
        if request == GET_STATUS:
            return b"\x00\x00"
        if request == GET_CONFIGURATION:
            return b"\x01"
        if request in (CLEAR_FEATURE, SET_FEATURE):
            return None
        raise Stall()

    def error(self, status=STATUS_ERR_STALLEDPKT):
        self.state = DFU_ERROR
        self.status = status
        raise Stall()

    def dfu(self, request, value, length, data):
        if request == DFU_GETSTATUS:
            return self.get_status()
        if request == DFU_GETSTATE:
            return bytes([self.state])
        if request == DFU_DETACH:
            self.event("detach")
            if self.state in (APP_IDLE, APP_DETACH):
                self.state = APP_DETACH
                return "switch" if self.will_detach else None
            return "leave" if self.on_detach == "disconnect" else None
        if self.state in (APP_IDLE, APP_DETACH):
            self.error()
        if request == DFU_ABORT:
            self.event("abort")
            self.state = DFU_IDLE
            self.upload_offset = 0
            self.dnload = bytearray()
            return None
        if request == DFU_CLRSTATUS:
            if self.state != DFU_ERROR:
                self.error()
            self.event("clrstatus")
            self.state = DFU_IDLE
            self.status = STATUS_OK
            return None
        if request == DFU_DNLOAD:
            return self.download(value, data)
        if request == DFU_UPLOAD:
            return self.upload(value, length)
        self.error()

    def get_status(self):
        poll = 0
        if self.state == DNLOAD_SYNC and self.write_error:
            self.write_error = False
            self.state = DFU_ERROR
            self.status = STATUS_ERR_WRITE
            self.event("write-error")
        elif self.state == DNLOAD_SYNC:
            self.state = DNBUSY
            poll = 1
        elif self.state == DNBUSY:
            self.state = DNLOAD_IDLE
        elif self.state == MANIFEST_SYNC:
            self.state = MANIFEST
            poll = 1
            if self.leaving:
                self.event("leave")
                return self.status_bytes(poll), "leave"
            self.event("manifest")
        elif self.state == MANIFEST:
            if self.manifest_tolerant:
                self.state = DFU_IDLE
            else:
                self.state = MANIFEST_WAIT_RESET
                if self.will_detach:
                    return self.status_bytes(poll), "leave"
        return self.status_bytes(poll)

    def status_bytes(self, poll):
        return struct.pack("<B3sBB", self.status, poll.to_bytes(3, "little"), self.state, 0)

    def download(self, block, data):
        if self.state not in (DFU_IDLE, DNLOAD_IDLE):
            self.error()
        if not data:
            if self.dfuse:
                self.leaving = True
            elif self.state != DNLOAD_IDLE:
                self.error()
            else:
                self.memory = bytearray(self.dnload)
            self.state = MANIFEST_SYNC
            return None
        self.state = DNLOAD_SYNC
        if self.dfuse and block == 0:
            self.dfuse_command(data)
            return None
        if self.dfuse and block == 1:
            self.error()
        # A data block. With fail_on_block, that block is not written and
        # the following GETSTATUS reports errWRITE.
        if self.blocks == self.fail_on_block:
            self.write_error = True
        elif self.dfuse:
            offset = self.dfuse_offset(block, len(data))
            if offset < 0 or offset + len(data) > len(self.memory):
                self.error(STATUS_ERR_ADDRESS)
            self.memory[offset:offset + len(data)] = data
        else:
            self.dnload += data
        self.blocks += 1
        return None

    def dfuse_offset(self, block, size):
        """Memory offset of DfuSe block number `block` (2 and up) of `size` bytes."""
        return self.address - self.dfuse_base + (block - 2) * size

    def erase(self, start, size):
        self.memory[start:start + size] = b"\xff" * size

    def dfuse_command(self, data):
        cmd = data[0]
        if cmd == 0x21 and len(data) == 5:
            self.address = int.from_bytes(data[1:5], "little")
        elif cmd == 0x41 and len(data) == 5:
            address = int.from_bytes(data[1:5], "little")
            page = (address - self.dfuse_base) // self.dfuse_page
            if not 0 <= page < self.dfuse_pages:
                self.error(STATUS_ERR_ADDRESS)
            self.erase(page * self.dfuse_page, self.dfuse_page)
            self.event("erase:0x%08x" % (self.dfuse_base + page * self.dfuse_page))
        elif cmd == 0x41 and len(data) == 1:
            self.erase(0, len(self.memory))
            self.event("mass-erase")
        else:
            self.error()

    def upload(self, block, length):
        if self.state not in (DFU_IDLE, UPLOAD_IDLE):
            self.error()
        if self.dfuse:
            if block < 2:
                self.error()
            offset = self.dfuse_offset(block, length)
            chunk = bytes(self.memory[max(offset, 0):offset + length])
        else:
            chunk = bytes(self.memory[self.upload_offset:self.upload_offset + length])
            self.upload_offset += len(chunk)
        self.state = UPLOAD_IDLE if len(chunk) == length else DFU_IDLE
        if self.state == DFU_IDLE:
            self.upload_offset = 0
        return chunk

    def bus_reset(self):
        """Returns "switch", "leave" or None."""
        self.event("reset")
        if self.runtime:
            return "switch"
        if self.on_reset == "disconnect":
            return "leave"
        self._reset_state()
        return None

    # USB/IP transport: plugging into vhci_hcd and serving its requests.

    def attach(self):
        self.sock, kernel_sock = socket.socketpair()
        self.port = free_port()
        (VHCI / "attach").write_text("%d %d %d %d" % (
            self.port, kernel_sock.fileno(), self.devid, USB_SPEED_HIGH))
        kernel_sock.close()
        self.event("attach")
        self.log("attach %04x:%04x on vhci port %d" % (self.vid, self.pid, self.port))
        self.attached = True
        self.thread = threading.Thread(target=self.serve, args=(self.sock,), daemon=True)
        self.thread.start()

    def detach(self):
        if not self.attached:
            return
        self.attached = False
        with suppress(OSError):
            (VHCI / "detach").write_text("%d" % self.port)
        with suppress(OSError):
            self.sock.close()
        self.event("disconnect")
        self.log("disconnect")

    def stop(self):
        self.detach()
        if self.thread:
            self.thread.join(timeout=5)

    def log(self, text):
        with self.lock:
            self.trace.append("%8.3f %s" % (time.monotonic() - self.t0, text))

    def later(self, action):
        def run():
            time.sleep(0.02)
            if action == "leave":
                self.detach()
            elif action == "switch":
                self.detach()
                time.sleep(0.2)
                self.enter_dfu_mode()
                self.attach()
        threading.Thread(target=run, daemon=True).start()

    def serve(self, sock):
        # Ends when vhci_hcd shuts down its end of the socket on detach,
        # which makes the read come back short.
        f = sock.makefile("rb")
        try:
            while True:
                header = recv_exact(f, HEADER_SIZE)
                command, seqnum, devid, direction, ep = BASIC.unpack_from(header)
                if command == USBIP_CMD_UNLINK:
                    sock.sendall(ret_unlink(seqnum, devid))
                    continue
                if command != USBIP_CMD_SUBMIT:
                    raise ConnectionError("unexpected USB/IP command %d" % command)
                _, length, _, _, _, setup = CMD_SUBMIT.unpack_from(header, BASIC.size)
                data = recv_exact(f, length) if direction != USBIP_DIR_IN and length > 0 else b""

                status, payload, action = 0, b"", None
                with self.lock:
                    try:
                        if ep != 0:
                            raise Stall()
                        result, action = split_result(self.control(setup, data))
                        if action == "reset":
                            action = self.bus_reset()
                            if action:
                                # The device drops off the bus during the reset.
                                self.log("%s -> bus reset, %s" % (describe(setup), action))
                                self.later(action)
                                continue
                            result = device_descriptor(self.vid, self.pid)
                        if direction == USBIP_DIR_IN:
                            payload = (result or b"")[:length]
                    except Stall:
                        status = -EPIPE

                actual = len(payload) if direction == USBIP_DIR_IN else (0 if status else length)
                self.log("%s %s -> %s" % ("IN " if direction == USBIP_DIR_IN else "OUT",
                                          describe(setup),
                                          "STALL" if status else "%d bytes" % actual))
                sock.sendall(ret_submit(seqnum, devid, direction, ep, status, actual, payload))
                if action:
                    self.later(action)
        except (ConnectionError, OSError):
            pass
        finally:
            f.close()
