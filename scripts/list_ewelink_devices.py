#!/usr/bin/env python3
"""List eWeLink cloud devices through the Home Assistant Sonoff integration."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from ewelink_common import device_model, load_sonoff_config


def clean(value: object) -> str:
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


async def run(args: argparse.Namespace) -> int:
    import aiohttp

    sys.path.insert(0, str(args.ha_config))
    from custom_components.sonoff.core.ewelink.cloud import XRegistryCloud

    config = load_sonoff_config(args.ha_config)
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        cloud = XRegistryCloud(session)
        await cloud.login(**config)
        devices = await cloud.get_devices()

    selected = [
        device
        for device in devices
        if not args.name
        or args.name.casefold() in clean(device.get("name")).casefold()
    ]
    if not selected:
        print("No matching eWeLink devices found.")
        return 1

    print("NAME\tDEVICE_ID\tMODEL\tONLINE")
    for device in sorted(selected, key=lambda item: clean(item.get("name")).casefold()):
        print(
            "\t".join(
                (
                    clean(device.get("name")),
                    clean(device.get("deviceid")),
                    clean(device_model(device)),
                    clean(device.get("online")),
                )
            )
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ha-config",
        type=Path,
        default="/config",
        help="Home Assistant configuration directory containing the Sonoff integration",
    )
    parser.add_argument(
        "--name",
        help="Only list devices whose name contains this text",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

