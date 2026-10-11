"""Forward one TCP port to another address -- the Windows Sandbox's port.

    python forward.py --listen 127.0.0.1:18756 --to 172.20.1.5:18756
    python forward.py --listen 127.0.0.1:18756 --to-file .sandbox/out/ready.json

Used twice by tools/sandbox (stdlib only, so the installed app's bundled
Python can run it inside the sandbox):

- inside the sandbox, <sandbox IP>:18756 -> 127.0.0.1:8756, so the app sees
  every request arrive from loopback;
- on this PC, 127.0.0.1:18756 -> <sandbox IP>:18756, so the browser asks for
  "localhost".

Both are needed for the admin view (Shop setup): AutoRundown treats a request
as admin only when it comes from loopback AND names a loopback host
(autorundown/auth.py). Bytes pass through unchanged, Host and Origin included.
--to-file waits for the sandbox to write its address ({"ip", "port"}).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path


def addr(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    return host.strip("[]"), int(port)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def serve(listen: tuple[str, int], target: tuple[str, int]) -> None:
    async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
        try:
            tr, tw = await asyncio.open_connection(*target)
        except OSError:
            cw.close()
            return
        await asyncio.gather(pipe(cr, tw), pipe(tr, cw))

    srv = await asyncio.start_server(handle, *listen)
    print(f"forwarding {listen[0]}:{listen[1]} -> {target[0]}:{target[1]}", flush=True)
    async with srv:
        await srv.serve_forever()


def wait_for_file(path: Path, timeout: float) -> tuple[str, int]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            d = json.loads(path.read_text(encoding="utf-8-sig"))
            return d["ip"], int(d["port"])
        except (OSError, ValueError, KeyError):
            time.sleep(1)
    raise SystemExit(f"no sandbox address in {path} after {timeout:.0f} s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--listen", required=True, help="host:port to listen on")
    to = ap.add_mutually_exclusive_group(required=True)
    to.add_argument("--to", help="host:port to forward to")
    to.add_argument("--to-file", help="ready.json the sandbox writes ({ip, port})")
    ap.add_argument("--wait", type=float, default=900, help="seconds to wait for --to-file")
    args = ap.parse_args()
    target = addr(args.to) if args.to else wait_for_file(Path(args.to_file), args.wait)
    try:
        asyncio.run(serve(addr(args.listen), target))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
