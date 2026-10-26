# cps-digital-linux

Linux driver for **CPS-Digital** USB temperature displays (the small digit
displays on CPS coolers / cases), which officially ship with Windows-only
software. It shows the live CPU temperature on the display, just like the
vendor app.

- USB IDs: `1a2c:444d`, `1a2c:4185`, `1a2c:4887`. The device shows up as
  "SEMICO USB Gaming Keyboard" in `lsusb`.
- No kernel module needed: it is a single Python 3 script using the stock
  `hidraw` driver, with no extra dependencies.
- Runs as a systemd user service and restarts automatically when the display
  is re-plugged.

## Install

```sh
git clone https://github.com/AFLOY/cps-digital-linux.git
cd cps-digital-linux
./install.sh          # asks for sudo once, to install the udev rule
```

That's all: the display starts showing the CPU temperature, and the service
starts at every login.

For Fahrenheit: `./install.sh -F`. Daemon options are stored in
`~/.config/cps-digital.conf`. After editing that file, run
`systemctl --user restart cps-digital`.

Uninstall: `./uninstall.sh`

## Manual use

```sh
cps-digital --list        # detected displays
cps-digital --info        # firmware version
cps-digital --dry-run     # print sensor values and packets, don't touch the device
cps-digital -F            # run in the foreground, Fahrenheit
```

The display goes blank if it stops receiving updates, so the daemon has to keep
running. `--once` is only for testing.

## Sensors

| value | source |
|---|---|
| CPU temperature | hwmon `k10temp`/`zenpower` Tctl, `coretemp` Package id 0, fallback `acpitz` |
| CPU usage | `/proc/stat` |
| CPU clock | `cpufreq scaling_cur_freq` (max over cores) |
| CPU power | RAPL `intel-rapl:0` (root only on recent kernels, otherwise 0) |
| CPU fan | first spinning `fanN_input` on the Super I/O chip, or `--fan PATH` |
| GPU temp / usage / power / clock | `amdgpu` hwmon, else `nvidia-smi` |
| memory usage | `/proc/meminfo` |

Types 1 and 2 only display the CPU temperature. The other values are used by
type 3.

## Protocol

Reverse-engineered from the vendor's `CPS-Digital setup 1.0.1.4.exe`. The
device is driven with HID **feature reports, report ID 7, 64 bytes**, on the
interface whose top-level collection is vendor usage page `0xFF01`, usage 1.
Updates are sent once per second.

| PID  | type | packets |
|------|------|---------|
| 444d | 1 | `07 FF FF h t o`: CPU temperature as 3 decimal digits (°C or °F, max 199) |
| 4185 | 2 | same as type 1 |
| 4887 | 3 | 5 pages `07 00` … `07 04`, sent 10 ms apart (see below) |

Type 3 pages: each byte after the page number holds two decimal digits, high
nibble = GPU and low nibble = CPU, unless noted otherwise.

| page | bytes 2‥7 |
|---|---|
| `07 00` | temperature×10 digits (thousands, hundreds, tens, ones), `mode<<4 \| fahrenheit`, CPU temperature level 1‥20 (5 °C steps) |
| `07 01` | usage % digits (hundreds, tens, ones), `00`, `GPU level<<4 \| CPU level` (1‥7, 14 % steps), `01` |
| `07 02` | power W digits (hundreds, tens, ones), `00 00 00` |
| `07 03` | CPU clock MHz thousands, then `memory%<<4 \| clock` digits for hundreds/tens/ones, clock level 1‥10 (700 MHz steps), `00` |
| `07 04` | CPU fan rpm digits (thousands … ones, one per byte), fan level 1‥10 (300 rpm steps), `00` |

Query: send `07 FD`, then a get-feature returns `07 hi lo`, the firmware
version.

## Status

| model | status |
|---|---|
| `1a2c:444d` (type 1) | tested, works (firmware 0x000b) |
| `1a2c:4185` (type 2) | untested, same protocol as type 1 |
| `1a2c:4887` (type 3) | untested, decoded from the binary only |

Reports for the untested models are welcome.

## Disclaimer

Not affiliated with or endorsed by CPS. The protocol was obtained by analysing
the vendor software for interoperability. No vendor code or binaries are
included in this repository.

---

## 日本語

CPS-Digital の USB 温度ディスプレイを Linux で動かすツールです。公式ソフトは
Windows 専用ですが、これを使うと CPU 温度を毎秒ディスプレイに送り続けます。

```sh
git clone https://github.com/AFLOY/cps-digital-linux.git
cd cps-digital-linux
./install.sh        # udev ルールの導入時に一度だけ sudo のパスワードを聞かれます
```

これでサービスの作成と起動までが自動で行われ、次回以降もログイン時に起動します。
華氏で表示したいときは `./install.sh -F`、アンインストールは `./uninstall.sh` です。
