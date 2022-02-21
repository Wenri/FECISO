import ast
from io import StringIO

import numpy as np


def sizeof_fmt(num, suffix="B"):
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


class DiscCapacity:
    _DiscName = ('DVD+R', 'DVD+R DL', 'BD-XL TL')
    _DiscSectors = (2295104, 4173824, 48878592)

    def __init__(self, ds):
        self.ds = ds
        self.disc_name, self.total_s = self._get_closest()

    def _get_closest(self):
        dist = np.array(self._DiscSectors, dtype=np.int_) - self.ds
        idx = np.flatnonzero(dist >= 0)
        if len(idx):
            i = np.argmin(dist[idx])
            return self._DiscName[idx[i]], self._DiscSectors[idx[i]]
        else:
            return None, -1


class NumberSegments:
    """
    if first:
        print(f'{i}-', end='')
    else:
        print(f'{i + 1}:{prev_str} {i}-', end='')
    prev_str = size_str
    """

    def __init__(self, rep_str, *init_vals):
        self.rep_str = sizeof_fmt(rep_str)
        self.number = set(init_vals)

    def __eq__(self, other):
        return self.rep_str == sizeof_fmt(other)

    def add_val(self, val):
        self.number.add(val)

    def __str__(self):
        if not self.number:
            return f'<empty>:{self.rep_str}'
        prev, *number = sorted(self.number, reverse=True)
        fold = False
        with StringIO() as s:
            print(prev, end='', file=s)
            for i in number:
                if prev == i + 1:
                    fold = True
                else:
                    if fold:
                        s.write(f'-{prev}')
                        fold = False
                    s.write(f',{i}')
                prev = i
            if fold:
                s.write(f'-{prev}')
            s.write(f':{self.rep_str}')
            return s.getvalue()


class VolID:
    def __init__(self, s: str):
        s = s.strip()
        if len(s) > 15 or not s.isascii() or not s.isidentifier():
            raise ValueError(s)
        self.s = s

    def get_volid(self):
        return self.s.upper()

    def get_dmid(self):
        return self.s.lower()


class DiscID:
    _KNOWN_IDS = {
        'VERBAT/IMk/0',
    }

    def __init__(self, s: str):
        s = s.strip()
        if not s.isascii() or s not in self._KNOWN_IDS:
            raise ValueError(s)
        self.s = s

    def __str__(self):
        return self.s


class PassHint:
    def __init__(self, s: str = 'Please input you password'):
        assert isinstance(ast.literal_eval(f'r"""{s}: """'), str)
        self.s = s

    def __str__(self):
        return self.s
