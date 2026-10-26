#!/usr/bin/env python3
"""Linux driver (userspace daemon) for CPS-Digital USB temperature displays.

Protocol reverse-engineered from the Windows "CPS-Digital setup 1.0.1.4.exe"
(DeviceDriver.exe).  The devices are USB HID (VID 0x1A2C, CRSC chip) and are
driven with 64-byte HID feature reports, report ID 7, on the vendor collection
(usage page 0xFF01).  No kernel module is needed: Linux hidraw is enough.

  PID 0x444D -> type 1 }  [07 FF FF h t o 00..]   CPU temperature, 3 digits
  PID 0x4185 -> type 2 }  (sent once per second)
  PID 0x4887 -> type 3    5 pages [07 NN ...] with CPU/GPU temp, usage,
                          power, clock, fan rpm and memory usage (BCD nibbles)
  query        [07 FD]  -> get feature: [07 hi lo] = firmware version
"""
import argparse
import fcntl
import glob
import os
import subprocess
import sys
import time

VID = 0x1A2C
PID_TYPES = {0x444D: 1, 0x4185: 2, 0x4887: 3}
REPORT_ID = 7
REPORT_LEN = 64


def _ioc(dir_, nr, size):
    return (dir_ << 30) | (size << 16) | (ord('H') << 8) | nr


HIDIOCSFEATURE = _ioc(3, 0x06, REPORT_LEN)
HIDIOCGFEATURE = _ioc(3, 0x07, REPORT_LEN)


# ---------------------------------------------------------------- device

def find_devices():
    """Yield (hidraw path, pid) for the vendor (report ID 7) interface."""
    for node in sorted(glob.glob('/sys/class/hidraw/hidraw*')):
        try:
            uevent = open(os.path.join(node, 'device/uevent')).read()
            rdesc = open(os.path.join(node, 'device/report_descriptor'), 'rb').read()
        except OSError:
            continue
        hid_id = next((l.split('=', 1)[1] for l in uevent.splitlines()
                       if l.startswith('HID_ID=')), '')
        try:
            _, vid, pid = (int(x, 16) for x in hid_id.split(':'))
        except ValueError:
            continue
        if vid != VID or pid not in PID_TYPES:
            continue
        # vendor usage page 0xFF01 and report ID 7
        if b'\x06\x01\xff' in rdesc and b'\x85\x07' in rdesc:
            yield '/dev/' + os.path.basename(node), pid


class Device:
    def __init__(self, path, pid):
        self.path, self.pid, self.type = path, pid, PID_TYPES[pid]
        self.fd = os.open(path, os.O_RDWR)

    def close(self):
        os.close(self.fd)

    def set_feature(self, payload):
        buf = bytearray(REPORT_LEN)
        buf[:len(payload)] = payload
        buf[0] = REPORT_ID
        fcntl.ioctl(self.fd, HIDIOCSFEATURE, bytes(buf))

    def get_feature(self):
        buf = bytearray(REPORT_LEN)
        buf[0] = REPORT_ID
        fcntl.ioctl(self.fd, HIDIOCGFEATURE, buf, True)
        return bytes(buf)

    def firmware_version(self):
        self.set_feature(bytes([REPORT_ID, 0xFD]))
        time.sleep(0.016)
        r = self.get_feature()
        if r[0] != REPORT_ID:
            r = self.get_feature()
        return (r[1] << 8) | r[2]


# --------------------------------------------------------------- sensors

def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _hwmons():
    for h in sorted(glob.glob('/sys/class/hwmon/hwmon*')):
        yield h, _read(h + '/name', '')


def _hwmon_temp(names, labels=None):
    for h, name in _hwmons():
        if name not in names:
            continue
        inputs = sorted(glob.glob(h + '/temp*_input'))
        if labels:
            for lab in labels:
                for inp in inputs:
                    if _read(inp.replace('_input', '_label'), '') == lab:
                        return int(_read(inp, '0')) / 1000.0
        if inputs:
            return int(_read(inputs[0], '0')) / 1000.0
    return 0.0


class Sensors:
    def __init__(self, fan_path=None):
        self._stat = None
        self._rapl = None
        self.fan_path = fan_path or self._guess_fan()

    @staticmethod
    def _guess_fan():
        # first spinning fan on the Super I/O chip (CPU_FAN is usually fan1)
        for h, name in _hwmons():
            if name.startswith(('it8', 'nct', 'f71', 'w83', 'asus', 'dell')):
                for f in sorted(glob.glob(h + '/fan*_input')):
                    if int(_read(f, '0')) > 0:
                        return f
        return None

    def cpu_temp(self):
        return _hwmon_temp(('k10temp', 'zenpower'), ('Tctl', 'Tdie')) or \
            _hwmon_temp(('coretemp',), ('Package id 0',)) or \
            _hwmon_temp(('cpu_thermal', 'acpitz'))

    def cpu_usage(self):
        v = [int(x) for x in open('/proc/stat').readline().split()[1:]]
        idle, total = v[3] + v[4], sum(v)
        prev, self._stat = self._stat, (idle, total)
        if not prev or total == prev[1]:
            return 0.0
        return 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))

    def cpu_clock(self):
        f = [int(_read(p, '0')) for p in
             glob.glob('/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq')]
        return max(f) / 1000.0 if f else 0.0

    def cpu_power(self):
        e = _read('/sys/class/powercap/intel-rapl:0/energy_uj')
        if e is None:      # needs root (or the udev rule) on recent kernels
            return 0.0
        now = (int(e), time.monotonic())
        prev, self._rapl = self._rapl, now
        if not prev or now[0] < prev[0]:
            return 0.0
        return (now[0] - prev[0]) / 1e6 / (now[1] - prev[1])

    def cpu_fan(self):
        return int(_read(self.fan_path, '0')) if self.fan_path else 0

    def gpu(self):
        """(temp, usage, power W, clock MHz); dGPU first, like the original."""
        for h, name in _hwmons():
            if name != 'amdgpu':
                continue
            dev = os.path.realpath(h + '/device')
            usage = float(_read(dev + '/gpu_busy_percent', '0'))
            power = int(_read(h + '/power1_average') or _read(h + '/power1_input') or 0) / 1e6
            clock = int(_read(h + '/freq1_input', '0')) / 1e6
            temp = _hwmon_temp_at(h)
            return temp, usage, power, clock
        try:
            out = subprocess.run(
                ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu,power.draw,clocks.gr',
                 '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=2).stdout
            t, u, p, c = (float(x) if x.strip()[:1].isdigit() else 0.0
                          for x in out.splitlines()[0].split(','))
            return t, u, p, c
        except (OSError, IndexError, ValueError, subprocess.SubprocessError):
            return 0.0, 0.0, 0.0, 0.0

    @staticmethod
    def mem_usage():
        m = {l.split(':')[0]: int(l.split()[1]) for l in open('/proc/meminfo')}
        return 100.0 * (1 - m['MemAvailable'] / m['MemTotal'])


def _hwmon_temp_at(h):
    inputs = sorted(glob.glob(h + '/temp*_input'))
    return int(_read(inputs[0], '0')) / 1000.0 if inputs else 0.0


# -------------------------------------------------------------- protocol

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _level(v, step, top):
    """Bar level used by the original: 0 for v<=0, else ceil(v/step) <= top."""
    return 0 if v <= 0 else _clamp(-(-v // step), 1, top)


def packet_simple(cpu_temp, fahrenheit):
    """Types 1/2: [07 FF FF h t o]."""
    t = int(cpu_temp)
    if fahrenheit:
        t = int(t * 1.8 + 32.0)
    t = _clamp(t, 0, 199)
    return [bytes([REPORT_ID, 0xFF, 0xFF, t // 100 % 10, t // 10 % 10, t % 10])]


def packets_type3(s, fahrenheit, mode):
    """Type 3: five pages, digits packed as nibbles (high=GPU, low=CPU)."""
    T = int(s['cpu_temp'] * 10)
    G = int(s['gpu_temp'] * 10)
    U, GU = s['cpu_usage'], s['gpu_usage']
    U = 1 if 0 < U < 1 else int(U)
    GU = 1 if 0 < GU < 1 else int(GU)
    P, GP = int(s['cpu_power']), int(s['gpu_power'])
    C, F, M = int(s['cpu_clock']), int(s['cpu_fan']), int(s['mem_usage'])

    lt = _level(T // 10, 5, 20)
    lc = _level(C, 700, 10)
    lf = _level(F, 300, 10)
    lu = _level(U, 14, 7)
    lg = _level(GU, 14, 7)

    if fahrenheit:
        T = int(T * 1.8 + 320) if T else 0
        G = int(G * 1.8 + 320) if G else 0
    T, G = _clamp(T, 0, 9999), _clamp(G, 0, 9999)
    U, GU, M = _clamp(U, 0, 100), _clamp(GU, 0, 100), _clamp(M, 0, 100)
    if T > 0 and U == 0:
        U = 1
    P, GP = _clamp(P, 0, 999), _clamp(GP, 0, 999)
    C, F = _clamp(C, 0, 9999), _clamp(F, 0, 9999)

    d = lambda v, n: v // n % 10            # decimal digit
    nib = lambda hi, lo: ((hi & 0xF) << 4) | (lo & 0xF)
    return [
        bytes([REPORT_ID, 0, nib(d(G, 1000), d(T, 1000)), nib(d(G, 100), d(T, 100)),
               nib(d(G, 10), d(T, 10)), nib(d(G, 1), d(T, 1)),
               nib(mode, int(fahrenheit)), lt]),
        bytes([REPORT_ID, 1, nib(d(GU, 100), d(U, 100)), nib(d(GU, 10), d(U, 10)),
               nib(d(GU, 1), d(U, 1)), 0, nib(lg, lu), 1]),
        bytes([REPORT_ID, 2, nib(d(GP, 100), d(P, 100)), nib(d(GP, 10), d(P, 10)),
               nib(d(GP, 1), d(P, 1)), 0, 0, 0]),
        bytes([REPORT_ID, 3, d(C, 1000), nib(d(M, 100), d(C, 100)),
               nib(d(M, 10), d(C, 10)), nib(d(M, 1), d(C, 1)), lc, 0]),
        bytes([REPORT_ID, 4, d(F, 1000), d(F, 100), d(F, 10), d(F, 1), lf, 0]),
    ]


def read_all(sensors):
    gt, gu, gp, gc = sensors.gpu()
    return dict(cpu_temp=sensors.cpu_temp(), cpu_usage=sensors.cpu_usage(),
                cpu_clock=sensors.cpu_clock(), cpu_power=sensors.cpu_power(),
                cpu_fan=sensors.cpu_fan(), gpu_temp=gt, gpu_usage=gu,
                gpu_power=gp, gpu_clock=gc, mem_usage=sensors.mem_usage())


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--list', action='store_true', help='list devices and exit')
    ap.add_argument('--info', action='store_true', help='print firmware version and exit')
    ap.add_argument('--dry-run', action='store_true',
                    help='print sensor values and packets, do not open the device')
    ap.add_argument('--device', help='hidraw node (default: auto-detect)')
    ap.add_argument('--type', type=int, choices=(1, 2, 3),
                    help='protocol type (default: from USB PID)')
    ap.add_argument('-F', '--fahrenheit', action='store_true')
    ap.add_argument('--mode', type=int, default=0, choices=(0, 1),
                    help="type 3 only: the app's 'device_type' display toggle")
    ap.add_argument('--fan', help='hwmon fanN_input to show (default: auto)')
    ap.add_argument('--interval', type=float, default=1.0)
    ap.add_argument('--once', action='store_true', help='send one update and exit')
    a = ap.parse_args()

    found = list(find_devices())
    if a.list:
        for path, pid in found:
            print(f'{path}  {VID:04x}:{pid:04x}  type {PID_TYPES[pid]}')
        return 0 if found else 1

    sensors = Sensors(a.fan)
    sensors.cpu_usage()
    sensors.cpu_power()

    dev = None
    if not a.dry_run:
        if a.device:
            pid = next((p for d, p in found if d == a.device), 0x444D)
            target = (a.device, pid)
        elif found:
            target = found[0]
        else:
            print('no CPS-Digital device found', file=sys.stderr)
            return 1
        try:
            dev = Device(*target)
        except PermissionError:
            print(f'permission denied on {target[0]}: install 70-cps-digital.rules '
                  '(see README) or run as root', file=sys.stderr)
            return 1
        if a.info:
            print(f'{dev.path} type {dev.type} firmware 0x{dev.firmware_version():04x}')
            return 0
    dtype = a.type or (dev.type if dev else (PID_TYPES[found[0][1]] if found else 1))

    try:
        while True:
            time.sleep(a.interval)
            s = read_all(sensors)
            if dtype == 3:
                pkts = packets_type3(s, a.fahrenheit, a.mode)
            else:
                pkts = packet_simple(s['cpu_temp'], a.fahrenheit)
            if a.dry_run:
                print(' '.join(f'{k}={v:.1f}' for k, v in s.items()))
                for p in pkts:
                    print('   ', p.hex(' '))
            else:
                for i, p in enumerate(pkts):
                    if i:
                        time.sleep(0.010)
                    dev.set_feature(p)
            if a.once:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if dev:
            dev.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
