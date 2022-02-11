import argparse
import subprocess
import sys

# -V FLY1 -o test.iso projtest
from pathlib import Path

from fecsetup import FECSetup


def mkisofs(*targs, **kwargs):
    args = ['xorriso', '-as', 'mkisofs', '-verbose', '-iso-level', '4', '-r', '-J', '-joliet-long', '-no-pad']
    for k, t in kwargs.items():
        args.append(f"-{k}")
        args.append(f'{t}')
    for t in targs:
        args.append(f"{t}")
    with subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as p:
        for s in p.stdout:
            sys.stdout.write(s)
    if p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, p.args)
    return p.returncode


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('data_dir', type=str, help='data environment')
    parser.add_argument('-o', '--output', type=Path, help='old foo help')
    opt = parser.parse_args()
    print(opt)
    return opt


def main(opt):
    opt.output.unlink(missing_ok=True)
    mkisofs(opt.data_dir, V='FLY1', o=opt.output)
    fec = FECSetup(opt.output)
    fec.formatfec()


if __name__ == '__main__':
    sys.exit(main(parse_args()))
