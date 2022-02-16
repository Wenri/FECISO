import numpy as np


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
