from nnps_kernels import *
from compyle.config import get_config
from compyle.api import declare, annotate
from compyle.parallel import serial, Elementwise, Reduction, Scan
from compyle.array import get_backend, wrap
from compyle.low_level import atomic_inc, cast
from math import floor
from time import time

import numpy as np
import compyle.array as carr


class NNPS(object):
    def __init__(self, x, y, h, xmax, ymax, backend=None):
        self.backend = backend
        self.num_particles = x.length
        self.x, self.y = x, y
        self.h = h

        cmax = np.array([floor(xmax / h), floor(ymax / h)], dtype=np.int32)
        self.max_key = 1 + flatten(cmax[0], cmax[1], 1 + cmax[1])
        self.qmax = 1 + cmax[1]

        # neighbor kernels
        self.find_neighbor_lengths = Elementwise(find_neighbor_lengths_knl,
                                                 backend=self.backend)
        self.find_neighbors = Elementwise(find_neighbors_knl,
                                          backend=self.backend)
        self.scan_start_indices = Scan(input=input_start_indices,
                                       output=output_start_indices,
                                       scan_expr="a+b", dtype=np.int32,
                                       backend=self.backend)
        self.init_arrays()

    def init_arrays(self):
        # sort arrays
        self.bin_counts = carr.zeros(self.max_key, dtype=np.int32,
                                     backend=self.backend)
        self.start_indices = carr.zeros(self.max_key, dtype=np.int32,
                                        backend=self.backend)
        self.keys = carr.zeros(self.num_particles, dtype=np.int32,
                               backend=self.backend)
        self.sorted_indices = carr.zeros(self.num_particles, dtype=np.int32,
                                         backend=self.backend)

        # neighbor arrays
        self.nbr_lengths = carr.zeros(self.num_particles, dtype=np.int32,
                                      backend=self.backend)
        self.nbr_starts = carr.zeros(self.num_particles, dtype=np.int32,
                                     backend=self.backend)
        self.nbrs = carr.zeros(2 * self.num_particles, dtype=np.int32,
                               backend=self.backend)

    def reset_arrays(self):
        # sort arrays
        self.bin_counts.fill(0)
        self.start_indices.fill(0)
        self.sorted_indices.fill(0)

        # neighbors array
        self.nbr_lengths.fill(0)
        self.nbr_starts.fill(0)

    def get_neighbors(self):
        self.find_neighbor_lengths(self.x, self.y, self.h, self.qmax,
                                   self.start_indices, self.sorted_indices,
                                   self.bin_counts, self.nbr_lengths,
                                   self.max_key)
        self.scan_start_indices(counts=self.nbr_lengths,
                                indices=self.nbr_starts)
        self.total_neighbors = int(self.nbr_lengths[-1] + self.nbr_starts[-1])
        self.nbrs.resize(self.total_neighbors)
        self.find_neighbors(self.x, self.y, self.h, self.qmax,
                            self.start_indices, self.sorted_indices,
                            self.bin_counts, self.nbr_starts,
                            self.nbrs, self.max_key)


class NNPSCountingSort(NNPS):
    def __init__(self, x, y, h, xmax, ymax, backend=None):
        super().__init__(x, y, h, xmax, ymax, backend=backend)
        # sort kernels
        self.count_bins = Elementwise(count_bins, backend=self.backend)
        self.sort_indices = Elementwise(sort_indices, backend=self.backend)

    def init_arrays(self):
        super().init_arrays()
        self.sort_offsets = carr.zeros(self.num_particles, dtype=np.int32,
                                       backend=self.backend)

    def reset_arrays(self):
        super().reset_arrays()
        # sort arrays
        self.sort_offsets.fill(0)

    def build(self):
        self.reset_arrays()
        self.count_bins(self.x, self.y, self.h, self.qmax, self.keys,
                        self.bin_counts, self.sort_offsets)
        self.scan_start_indices(counts=self.bin_counts,
                                indices=self.start_indices)
        self.sort_indices(self.keys, self.sort_offsets, self.start_indices,
                          self.sorted_indices)


class NNPSRadixSort(NNPS):
    def __init__(self, x, y, h, xmax, ymax, backend=None):
        super().__init__(x, y, h, xmax, ymax, backend=backend)
        self.max_bits = np.ceil(np.log2(self.max_key))

        # sort kernels
        self.fill_keys = Elementwise(fill_keys, backend=self.backend)
        self.fill_bin_counts = Elementwise(fill_bin_counts,
                                           backend=self.backend)
        self.scan_keys = Scan(input=input_scan_keys,
                              output=output_scan_keys,
                              scan_expr="a+b", dtype=np.int32,
                              backend=self.backend)

    def init_arrays(self):
        super().init_arrays()
        # sort arrays
        self.sorted_keys = carr.zeros(self.num_particles, dtype=np.int32,
                                      backend=self.backend)
        self.indices = carr.zeros(self.num_particles, dtype=np.int32,
                                  backend=self.backend)

    def reset_arrays(self):
        super().reset_arrays()
        self.sorted_keys.fill(0)

    def build(self):
        self.reset_arrays()
        self.fill_keys(self.x, self.y, self.h, self.qmax, self.indices,
                       self.keys)
        self.sorted_keys, self.sorted_indices = carr.sort_by_keys(
            [self.keys, self.indices],
            key_bits=self.max_bits, backend=self.backend)
        self.scan_keys(keys=self.sorted_keys,
                       start_indices=self.start_indices)
        self.fill_bin_counts(self.sorted_keys, self.start_indices,
                             self.bin_counts, self.num_particles)


if __name__ == "__main__":
    import sys
    backend = sys.argv[1] if len(sys.argv) > 1 else 'cython'
    np.random.seed(123)
    num_particles = 20
    x = np.random.uniform(0, 10., size=num_particles).astype(np.float32)
    y = np.random.uniform(0, 10., size=num_particles).astype(np.float32)
    x, y = wrap(x, y, backend=backend)
    nnps = NNPSRadixSort(x, y, 3., 10., 10., backend=backend)
    nnps.build()
    nnps.get_neighbors()
    print(nnps.start_indices)
    print(nnps.bin_counts)
    print(nnps.nbr_lengths)
