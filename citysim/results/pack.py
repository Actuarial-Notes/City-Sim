"""Binary replay-frame packing.

One wire format, two producers: the live API (`GET /api/results/{job}/runs/{n}
/frames`) and the static export (`scripts/export_static.py`). Keeping the
packing here means the prebaked demo and the live server hand the viewer byte-
identical payloads, so the front end has a single decode path.

Layout::

    [uint32 header_len][header JSON][uint16 depth mm]  (little-endian)

Depth grids are quantised to millimetres — visually lossless for a depth
overlay and 4x smaller than float64 JSON before gzip.
"""

from __future__ import annotations

import json
import struct

import numpy as np


def pack_frames(depth, t, rain_mmh, surcharging, scenario: dict,
                run_summary: dict) -> bytes:
    """Pack one run's replay frames into the viewer's binary format.

    ``depth`` is (n_frames, ny, nx) in metres; ``surcharging`` is a per-frame
    list of surcharging node ids.
    """
    grids = np.clip(np.asarray(depth, dtype=np.float32) * 1000.0,
                    0, 65535).astype("<u2")
    n_frames, ny, nx = grids.shape
    header = json.dumps({
        "n_frames": int(n_frames), "ny": int(ny), "nx": int(nx),
        "t": np.asarray(t, dtype=np.float64).round(1).tolist(),
        "rain_mmh": np.asarray(rain_mmh, dtype=np.float64).round(2).tolist(),
        "surcharging": surcharging,
        "scenario": scenario,
        "run_summary": run_summary,
    }).encode()
    return struct.pack("<I", len(header)) + header + grids.tobytes()
