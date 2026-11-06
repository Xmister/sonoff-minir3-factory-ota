#!/usr/bin/env python3
"""Send a targeted eWeLink cloud upgrade command from the Home Assistant runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

from ewelink_common import device_model, load_sonoff_config


async def run(args: argparse.Namespace) -> int:
    import aiohttp

    sys.path.insert(0, str(args.ha_config))
    from custom_components.sonoff.core.ewelink.cloud import XRegistryCloud

    config = load_sonoff_config(args.ha_config)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        cloud = XRegistryCloud(session)
        await cloud.login(**config)
        device = next(
            (item for item in await cloud.get_devices() if item.get("deviceid") == args.device_id),
            None,
        )
        if device is None:
            raise RuntimeError(f"device {args.device_id} is not present in the eWeLink account")
        model = device_model(device)
        if model != args.model:
            raise RuntimeError(
                f"refusing model {model!r}; expected {args.model!r}"
            )
        if not await cloud.connect():
            raise RuntimeError("could not open the eWeLink cloud websocket")

        sequence = str(int(time.time() * 1000))
        message = {
            "action": "upgrade",
            "deviceid": device["deviceid"],
            "apikey": device["apikey"],
            "selfApikey": cloud.auth["user"]["apikey"],
            "userAgent": "app",
            "sequence": sequence,
            "params": {
                "model": args.model,
                "version": args.version,
                "binList": [
                    {
                        "name": args.name,
                        "downloadUrl": args.url,
                        "digest": args.sha256,
                    }
                ],
            },
        }
        await cloud.ws.send_json(message)
        print(
            f"Sent upgrade command for {device.get('name')} ({device['deviceid']}): "
            f"{args.version}, {args.sha256}",
            flush=True,
        )

        deadline = asyncio.get_running_loop().time() + args.timeout
        while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
            try:
                response = await asyncio.wait_for(cloud.ws.receive_json(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if response.get("sequence") != sequence:
                continue
            print(json.dumps(response, sort_keys=True), flush=True)
            return 0 if response.get("error", 0) == 0 else 1
        print("No matching cloud response before timeout.", file=sys.stderr)
        return 2

    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ha-config",
        type=Path,
        default="/config",
        help="Home Assistant configuration directory containing the Sonoff integration",
    )
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--model", default="BL1-S04-01")
    parser.add_argument("--version", required=True)
    parser.add_argument("--name", default="firmware.bin")
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--timeout", type=float, default=30)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
