#!/usr/bin/env python3

import argparse
import asyncio
import sys
from pathlib import Path

from beartype import beartype

from fecsetup import FECSetup, mkisofs, VolID


@beartype
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('data_dir', type=str, help='data environment')
    parser.add_argument('-o', '--output', type=Path, required=True, help='output iso file')
    parser.add_argument('-V', '--volid', type=VolID, required=True, help='volume label')
    return parser.parse_args()


async def main(opt: argparse.Namespace) -> int:
    opt.output.unlink(missing_ok=True)
    ret = await mkisofs(opt.data_dir, V=opt.volid.get_volid(), o=opt.output)
    if ret:
        return ret
    fec = FECSetup(opt.output, dmid=opt.volid)
    ret = await fec.formatfec()
    return ret


if __name__ == '__main__':
    sys.exit(asyncio.run(main(parse_args())))
