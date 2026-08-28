#!/usr/bin/env python3
"""Run every check in SPEC/GRN-V1.md over a directory of .grn files.

    python tools/validate.py <dir> [--lax]

Two layers, and both must pass for a file to count:

1.  The CONTAINER checks the spec has carried since v1 (magic, header size
    field, three sections, directory tiling, first-record identity, end
    marker on EOF, subtree nesting).  These are enforced inside grn.py's
    container pass and re-stated here so a regression is visible as itself
    rather than as a downstream semantic error.

2.  The SEMANTIC layer added with the payload work (SPEC 6-12): the string
    table and property list resolve; the skeleton parents precede their
    children and the identity chain (0c02 -> 0b00 -> object) is a proper
    permutation; mesh face indices stay inside their pools; texcoord corner
    rows cover every triangle exactly once; skin rows tile their record and
    stay inside the binding table; material texture references resolve;
    animation tracks tile, their key times are sorted, and their node slots
    resolve to bones.  All of these raise loudly inside grn.parse().

Content statistics that are OBSERVATIONS rather than requirements (weight
rows summing to 1, unit normals) are counted and reported but do not fail a
file; the spec records their corpus rates.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grn  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = sys.argv[1]
    strict = "--lax" not in sys.argv[2:]
    files = []
    for dp, _dn, fns in os.walk(root):
        files += [os.path.join(dp, fn) for fn in fns
                  if fn.lower().endswith(".grn")]
    if not files:
        print("no .grn files under %s" % root)
        return 2

    passed = failed = 0
    vers = {}
    n_mesh = n_anim = n_tris = n_tracks = 0
    odd_weights = odd_normals = 0
    for p in sorted(files):
        try:
            f = grn.parse(p, strict=strict)
        except Exception as ex:
            failed += 1
            print("  FAIL %-30s %s" % (os.path.basename(p), ex))
            continue
        passed += 1
        vers[f.version] = vers.get(f.version, 0) + 1
        n_mesh += bool(f.meshes)
        n_anim += bool(f.animation.tracks)
        n_tracks += len(f.animation.tracks)
        for m in f.meshes:
            n_tris += len(m.faces)
            for row in m.influences:
                if abs(sum(w for _s, w in row) - 1.0) > 1e-3:
                    odd_weights += 1
            for n in m.normals:
                ln = math.sqrt(sum(v * v for v in n))
                if math.isnan(ln) or not 0.99 < ln < 1.01:
                    odd_normals += 1

    print("%d files, %d passed every check, %d failed" % (len(files), passed, failed))
    print("versions seen: %s" % vers)
    print("%d files with meshes (%d triangles), %d with animations (%d tracks)"
          % (n_mesh, n_tris, n_anim, n_tracks))
    if odd_weights or odd_normals:
        print("observations (not failures): %d weight rows not summing to 1, "
              "%d non-unit normals" % (odd_weights, odd_normals))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
