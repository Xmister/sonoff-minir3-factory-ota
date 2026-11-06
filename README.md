# Sonoff MINI R3 factory-OTA tooling

Tools used to package and deliver a replacement BL602 firmware image through
the factory eWeLink OTA path on a Sonoff MINI R3.

This repository is specific to the BL602 Sonoff MINI R3 layout that reports
model `BL1-S04-01`. Do not use these packages for another board or for a
generic BL602 layout.

## Safety and recovery

Keep a verified full-flash backup and a working UART ROM-ISP recovery method
before starting. The original MINI R3 layout has its active firmware at
`0x010000` and its smaller inactive OTA slot at `0x13C000` (`0xB4000` bytes).
The factory OTA updater removes the `mfg` partition entry that overlaps the
inactive slot.

Work with mains disconnected while connecting UART or changing boot straps.
Do not connect GPIO8 directly to 3.3 V; use a 10 kΩ pull-up. Avoid relying on
the factory OTA path as the only recovery method.

## Prerequisites

- Python 3.11 or newer
- `pip install -r requirements.txt`
- A verified 2 MiB flash backup from the target MINI R3
- The vendor `eflash_loader_rc32m.bin` matching the BL602 ROM
- A firmware build that produces `image_firmware_staged.bin`
- Home Assistant with the Sonoff/eWeLink integration for the cloud trigger
- An HTTP endpoint reachable by the device

Do not commit flash dumps, firmware bundles, Home Assistant storage, API keys,
or eWeLink credentials.

## 1. Back up and identify the flash layout

Put the device in BL602 UART ROM-ISP mode, then:

```sh
python3 scripts/backup_bl602.py \
  --port /dev/ttyUSB0 \
  --loader /path/to/eflash_loader_rc32m.bin

python3 scripts/inspect_partition_table.py \
  ~/.local/share/bl602-flash-backups/minir3-bl602-*.bin
```

The backup script is deliberately read-only. It verifies the dump with the
chip-side SHA-256 before writing the final backup file.

## 2. Build the BL602 OTA package

For the current staged LibreTiny image:

```sh
python3 scripts/build_minir3_staged_ota.py \
  --backup /path/to/verified-backup.bin \
  --staged-image /path/to/image_firmware_staged.bin \
  --hardware-version MINIR3 \
  --software-version 2026.11.6.3 \
  --output minir3.ota
```

This validates the source backup, staged-image manifest, CRCs, compressed
payload hash, and inactive-slot capacity before wrapping the image in the
`BL60X_OTA_Ver1.0` XZ package.

`scripts/build_minir3_ota.py` is also included for non-staged images:

```sh
python3 scripts/build_minir3_ota.py \
  --backup /path/to/verified-backup.bin \
  --application /path/to/firmware.app.bin \
  --hardware-version MINIR3 \
  --software-version 2026.11.6.3 \
  --output minir3.ota
```

## 3. Wrap it for eWeLink

```sh
python3 scripts/build_ewelink_bl602_bundle.py \
  --payload minir3.ota \
  --output firmware.bundle

sha256sum firmware.bundle
```

## 4. Serve the bundle

Serve the file with byte-range support:

```sh
python3 scripts/serve_range.py \
  --file firmware.bundle \
  --bind 0.0.0.0 \
  --port 18767 \
  --range-status 206
```

Expose that port through an HTTPS reverse proxy or tunnel that the MINI R3 can
reach. Confirm the public URL returns the same SHA-256 shown in the previous
step.

## 5. Trigger the cloud upgrade

Run the trigger in the Home Assistant environment that already has an
authenticated Sonoff integration:

```sh
python3 scripts/ewelink_upgrade.py \
  --ha-config /config \
  --device-id <eweLink-device-id> \
  --model BL1-S04-01 \
  --version 2026.11.6.3 \
  --url https://ota.example.test/firmware.bundle \
  --sha256 <bundle-sha256>
```

The script refuses a different model, reads the existing integration
credentials rather than storing them, sends one targeted upgrade command, and
prints the matching cloud response.

## Files

| File | Purpose |
| --- | --- |
| `scripts/backup_bl602.py` | Read-only, hash-verified BL602 flash backup |
| `scripts/inspect_partition_table.py` | Validate BFPT tables and show the FW layout |
| `scripts/package_ota.py` | Build the low-level `BL60X_OTA_Ver1.0` container |
| `scripts/build_minir3_ota.py` | Build a MINIR3-specific image and raw serial image |
| `scripts/build_minir3_staged_ota.py` | Wrap a LibreTiny staged image for factory OTA |
| `scripts/build_ewelink_bl602_bundle.py` | Build the multi-component eWeLink wrapper |
| `scripts/serve_range.py` | Serve one bundle over HTTP with byte ranges |
| `scripts/ewelink_upgrade.py` | Send the targeted eWeLink cloud upgrade command |

Run the local checks with:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

