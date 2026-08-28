#!/usr/bin/env python3
"""grn2gltf.py -- convert Granny 1 `.grn` files to glTF 2.0 (.glb) or OBJ.

Built ON TOP of grn.py, which does all the format work; this file only
assembles modern-format output.  Standard library only, like the parser.

    python grn2gltf.py MODEL.grn -o out.glb
    python grn2gltf.py MODEL.grn --anim WALK.grn --anim IDLE.grn -o out.glb
    python grn2gltf.py MODEL.grn --obj out.obj
    python grn2gltf.py MODEL.grn --texture skin.png -o out.glb

WHAT THE CONVERTER DECIDES (and the parser deliberately does not):

Coordinates.  The corpus is authored in 3ds Max: Z-up, right-handed.  glTF is
Y-up, right-handed.  The conversion applied here is a single -90 degree
rotation about X at the skeleton root -- (x, y, z) -> (x, z, -y) in effect --
plus a uniform scale (default 0.0254: the files are in inches, glTF is
nominally metres).  Vertex data is written unmodified; the root node's
transform and the inverse bind matrices carry the conversion, so the numbers
in the buffers stay exactly the parser's.

Vertices.  A `.grn` mesh stores positions, normals and texture coordinates as
independent pools with per-corner indices.  glTF wants one index per vertex,
so corners are welded on their (position, normal, texcoord) index triple --
the same expansion RAD's own runtime performs (1,555 texcoord corners of
SB_Model.grn weld to the 1,030 vertices the game's renderer receives).

Skinning.  Every mesh becomes a skinned primitive over the file's full
skeleton.  Genuinely skinned meshes keep their stored weights (up to 4 per
vertex); rigid meshes are bound whole to the bone the file binds them to.
Weights that do not sum to 1 are renormalised HERE, in the converter, with a
note -- one JN vs JN file ships rows summing to 0.

Animation.  Tracks target bones BY NAME, because an animation file's rig is a
superset of the model's (walk rigs carry Dummy helpers the model lacks).
Track-relative key times become glTF samplers; 3x3 scale keys are reduced to
their diagonal (the corpus is 99.8% identity/diagonal; a non-diagonal scale
is reported and its diagonal used).

Textures are RAD-compressed inside the `.grn` (see SPEC 11) and are NOT
decoded; pass --texture to bind an externally recovered image (e.g. from the
sibling `.omt` container) to the primary material.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grn  # noqa: E402

# -- 90 degrees about X: Max Z-up -> glTF Y-up
ROOT_ROTATION = (-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))
INCH = 0.0254


# ---------------------------------------------------------------------------
# small matrix kit (row-major 3x3 + translation)

def quat_to_mat3(q):
    x, y, z, w = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def mat3_mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)]
            for r in range(3)]


def bone_local(b):
    """(M, t) local transform: rotation * scale, plus translation."""
    s = [list(b.scale[0:3]), list(b.scale[3:6]), list(b.scale[6:9])]
    return mat3_mul(quat_to_mat3(b.rotation), s), list(b.position)


def compose(parent, local):
    pm, pt = parent
    lm, lt = local
    m = mat3_mul(pm, lm)
    t = [pt[r] + sum(pm[r][k] * lt[k] for k in range(3)) for r in range(3)]
    return m, t


def invert(mt):
    m, t = mt
    a, b, c = m[0]
    e, f, g = m[1]
    h, i, j = m[2]
    det = a * (f * j - g * i) - b * (e * j - g * h) + c * (e * i - f * h)
    if abs(det) < 1e-12:
        raise ValueError("singular bind matrix")
    inv = [
        [(f * j - g * i) / det, (c * i - b * j) / det, (b * g - c * f) / det],
        [(g * h - e * j) / det, (a * j - c * h) / det, (c * e - a * g) / det],
        [(e * i - f * h) / det, (b * h - a * i) / det, (a * f - b * e) / det],
    ]
    it = [-sum(inv[r][k] * t[k] for k in range(3)) for r in range(3)]
    return inv, it


def world_binds(bones):
    world = {}
    for b in bones:
        loc = bone_local(b)
        world[b.index] = loc if b.parent == b.index else compose(world[b.parent], loc)
    return world


# ---------------------------------------------------------------------------
# vertex welding

def weld(mesh, notes):
    """Expand per-corner index triples into welded vertices + triangle list."""
    key_to_id = {}
    verts = []           # (pos_i, nrm_i, uv_i)
    tris = []
    for face in mesh.faces:
        corner_ids = []
        for k in range(3):
            key = (face.pos[k],
                   face.normal[k] if face.normal else -1,
                   face.texcoord[k] if face.texcoord else -1)
            vid = key_to_id.get(key)
            if vid is None:
                vid = len(verts)
                key_to_id[key] = vid
                verts.append(key)
            corner_ids.append(vid)
        # Max windings render clockwise in the game's mirrored space; glTF is
        # counter-clockwise front-face, and the runtime capture confirmed the
        # reversal (SPEC 8), so corners 0 and 1 swap here.
        tris.append((corner_ids[1], corner_ids[0], corner_ids[2]))

    positions, normals, uvs, joints, weights = [], [], [], [], []
    nrm_bad = 0
    for pos_i, nrm_i, uv_i in verts:
        positions.append(mesh.positions[pos_i])
        if nrm_i >= 0:
            n = mesh.normals[nrm_i]
            ln = math.sqrt(sum(v * v for v in n))
            if not (0.99 < ln < 1.01) or any(math.isnan(v) for v in n):
                nrm_bad += 1
                n = (0.0, 0.0, 1.0)
            normals.append(n)
        else:
            normals.append((0.0, 0.0, 1.0))
        if uv_i >= 0:
            u, v, _w = mesh.texcoords[uv_i]
            uvs.append((u, v))
        if mesh.influences:
            row = mesh.influences[pos_i]
        else:
            row = [(0, 1.0)]     # rigid: slot 0 is the mesh's only binding
        row = row[:4]
        total = sum(w for _s, w in row)
        if abs(total - 1.0) > 1e-3:
            notes.add("renormalised %s weight rows that did not sum to 1"
                      % mesh.name)
            if total <= 1e-6:
                row = [(row[0][0], 1.0)]
            else:
                row = [(s, w / total) for s, w in row]
        row = row + [(0, 0.0)] * (4 - len(row))
        joints.append(tuple(mesh.bindings[s].bone if w > 0 else 0
                            for s, w in row))
        weights.append(tuple(w for _s, w in row))
    if nrm_bad:
        notes.add("%d degenerate normals replaced with +Z in %s"
                  % (nrm_bad, mesh.name))
    if not mesh.texcoords:
        uvs = []
    return positions, normals, uvs, joints, weights, tris


# ---------------------------------------------------------------------------
# glb assembly

class Bin:
    def __init__(self):
        self.blob = bytearray()
        self.views = []
        self.accessors = []

    def push(self, data, target=None):
        while len(self.blob) % 4:
            self.blob.append(0)
        off = len(self.blob)
        self.blob += data
        view = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            view["target"] = target
        self.views.append(view)
        return len(self.views) - 1

    def accessor(self, view, ctype, count, atype, minmax=None, normalized=False):
        a = {"bufferView": view, "componentType": ctype,
             "count": count, "type": atype}
        if minmax:
            a["min"], a["max"] = minmax
        if normalized:
            a["normalized"] = True
        self.accessors.append(a)
        return len(self.accessors) - 1

    def vec_accessor(self, vals, atype, target=34962, with_minmax=False):
        n = len(vals[0])
        data = struct.pack("<%df" % (len(vals) * n),
                           *[c for v in vals for c in v])
        view = self.push(data, target)
        mm = None
        if with_minmax:
            mm = ([min(v[k] for v in vals) for k in range(n)],
                  [max(v[k] for v in vals) for k in range(n)])
        return self.accessor(view, 5126, len(vals), "VEC%d" % n, mm)

    def scalar_accessor(self, vals):
        data = struct.pack("<%df" % len(vals), *vals)
        view = self.push(data)
        return self.accessor(view, 5126, len(vals), "SCALAR",
                             ([min(vals)], [max(vals)]))


def diagonal_of(scale9, notes, where):
    offdiag = max(abs(scale9[k]) for k in (1, 2, 3, 5, 6, 7))
    if offdiag > 1e-4:
        notes.add("non-diagonal scale on %s reduced to its diagonal" % where)
    return (scale9[0], scale9[4], scale9[8])


def convert(model_path, anim_paths, out_path, texture_path=None,
            scale=INCH, verbose=True):
    notes = set()
    f = grn.parse(model_path)
    if not f.meshes:
        raise SystemExit("%s has no meshes; nothing to convert" % model_path)
    stem = os.path.splitext(os.path.basename(model_path))[0]

    b = Bin()
    gltf = {
        "asset": {"version": "2.0",
                  "generator": "grn2gltf.py (git.exentt.com/scotty/grn-re)"},
        "scene": 0,
        "scenes": [{"name": stem, "nodes": []}],
        "nodes": [],
        "meshes": [],
        "skins": [],
        "materials": [],
        "accessors": [],
        "bufferViews": [],
        "buffers": [],
        "animations": [],
    }

    # ---- nodes: bones become the node hierarchy; root gets the axis fix
    bone_node = {}
    for bone in f.bones:
        node = {"name": bone.name or ("bone_%d" % bone.index),
                "translation": list(bone.position),
                "rotation": list(bone.rotation)}
        sc = diagonal_of(bone.scale, notes, bone.name)
        if sc != (1.0, 1.0, 1.0):
            node["scale"] = list(sc)
        bone_node[bone.index] = len(gltf["nodes"])
        gltf["nodes"].append(node)
    for bone in f.bones:
        if bone.parent != bone.index:
            parent = gltf["nodes"][bone_node[bone.parent]]
            parent.setdefault("children", []).append(bone_node[bone.index])
    root = {"name": "%s (Z-up to Y-up)" % stem,
            "rotation": list(ROOT_ROTATION),
            "scale": [scale] * 3,
            "children": [bone_node[f.bones[0].index]] if f.bones else []}
    root_id = len(gltf["nodes"])
    gltf["nodes"].append(root)
    gltf["scenes"][0]["nodes"].append(root_id)

    # ---- skin: all bones, IBMs from the composed bind pose
    world = world_binds(f.bones)
    ibms = []
    for bone in f.bones:
        im, it = invert(world[bone.index])
        ibms.append((im[0][0], im[1][0], im[2][0], 0.0,
                     im[0][1], im[1][1], im[2][1], 0.0,
                     im[0][2], im[1][2], im[2][2], 0.0,
                     it[0], it[1], it[2], 1.0))
    ibm_data = struct.pack("<%df" % (16 * len(ibms)),
                           *[c for m in ibms for c in m])
    ibm_acc = b.accessor(b.push(ibm_data), 5126, len(ibms), "MAT4")
    gltf["skins"].append({
        "name": stem,
        "joints": [bone_node[bn.index] for bn in f.bones],
        "inverseBindMatrices": ibm_acc,
        "skeleton": bone_node[f.bones[0].index],
    })

    # ---- materials
    def material_for(mesh):
        mi = mesh.material_index
        # doubleSided, deliberately: attachment bones (SpongeBob's eye and
        # nose boxes) carry mirroring scales, which flip those triangles'
        # winding; the 2002 renderer did not cull and neither should we
        mat = {"name": "default", "doubleSided": True,
               "pbrMetallicRoughness": {"metallicFactor": 0.0,
                                        "roughnessFactor": 0.9}}
        if 0 <= mi < len(f.materials):
            src = f.materials[mi]
            mat["name"] = src.name or ("material_%d" % mi)
            if src.kind == "color" and src.color:
                mat["pbrMetallicRoughness"]["baseColorFactor"] = [
                    max(0.0, min(1.0, c)) for c in src.color]
            elif src.kind == "texture":
                ti = tex_index.get(src.texture_name, tex_index.get("*"))
                if ti is not None:
                    mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": ti}
        elif "*" in tex_index:
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": tex_index["*"]}
        gltf["materials"].append(mat)
        return len(gltf["materials"]) - 1

    # texture_path: a single path (bound to every textured material) or a
    # dict {texture object name -> path}, with "*" as a catch-all key
    tex_index = {}
    tex_files = {}
    if isinstance(texture_path, str):
        tex_files = {"*": texture_path}
    elif texture_path:
        tex_files = dict(texture_path)
    if tex_files:
        gltf["images"] = []
        gltf["samplers"] = [{"magFilter": 9729, "minFilter": 9987,
                             "wrapS": 10497, "wrapT": 10497}]
        gltf["textures"] = []
        for key, path in tex_files.items():
            with open(path, "rb") as fh:
                img = fh.read()
            mime = "image/png" if img[:4] == b"\x89PNG" else "image/jpeg"
            view = b.push(img)
            gltf["images"].append({"bufferView": view, "mimeType": mime,
                                   "name": os.path.basename(path)})
            gltf["textures"].append({"source": len(gltf["images"]) - 1,
                                     "sampler": 0})
            tex_index[key] = len(gltf["textures"]) - 1

    # ---- meshes
    for mesh in f.meshes:
        positions, normals, uvs, joints, weights, tris = weld(mesh, notes)
        attrs = {
            "POSITION": b.vec_accessor(positions, None, with_minmax=True),
            "NORMAL": b.vec_accessor(normals, None),
            "JOINTS_0": None, "WEIGHTS_0": None,
        }
        jdata = struct.pack("<%dH" % (len(joints) * 4),
                            *[j for row in joints for j in row])
        attrs["JOINTS_0"] = b.accessor(b.push(jdata, 34962), 5123,
                                       len(joints), "VEC4")
        attrs["WEIGHTS_0"] = b.vec_accessor(weights, None)
        if uvs:
            attrs["TEXCOORD_0"] = b.vec_accessor(uvs, None)
        idata = struct.pack("<%dI" % (len(tris) * 3),
                            *[i for t in tris for i in t])
        idx = b.accessor(b.push(idata, 34963), 5125, len(tris) * 3, "SCALAR")
        prim = {"attributes": attrs, "indices": idx,
                "material": material_for(mesh)}
        gltf["meshes"].append({"name": mesh.name or "mesh",
                               "primitives": [prim]})
        gltf["nodes"].append({"name": mesh.name or "mesh",
                              "mesh": len(gltf["meshes"]) - 1,
                              "skin": 0})
        gltf["scenes"][0]["nodes"].append(len(gltf["nodes"]) - 1)

    # ---- animations
    by_name = {bn.name: bn for bn in f.bones}
    for path in anim_paths:
        af = grn.parse(path)
        aname = os.path.splitext(os.path.basename(path))[0]
        channels, samplers = [], []

        def sampler(times, values, atype):
            # glTF wants strictly increasing input; drop exact repeats
            ts, vs = [], []
            for t, v in zip(times, values):
                if ts and t <= ts[-1]:
                    continue
                ts.append(t)
                vs.append(v)
            if not ts:
                return None
            sa = {"input": b.scalar_accessor(ts),
                  "output": b.vec_accessor(vs, atype),
                  "interpolation": "LINEAR"}
            samplers.append(sa)
            return len(samplers) - 1

        used = 0
        for tr in af.animation.tracks:
            bone = by_name.get(tr.name)
            if bone is None:
                continue
            used += 1
            node = bone_node[bone.index]
            if tr.positions:
                s = sampler(tr.pos_times, [list(p) for p in tr.positions], None)
                if s is not None:
                    channels.append({"sampler": s,
                                     "target": {"node": node, "path": "translation"}})
            if tr.rotations:
                qs = []
                prev = None
                for q in tr.rotations:
                    q = list(q)
                    if prev and sum(a * c for a, c in zip(q, prev)) < 0:
                        q = [-c for c in q]
                    qs.append(q)
                    prev = q
                s = sampler(tr.rot_times, qs, None)
                if s is not None:
                    channels.append({"sampler": s,
                                     "target": {"node": node, "path": "rotation"}})
            if tr.scales:
                diags = [list(diagonal_of(sc, notes, "%s/%s" % (aname, tr.name)))
                         for sc in tr.scales]
                s = sampler(tr.scale_times, diags, None)
                if s is not None:
                    channels.append({"sampler": s,
                                     "target": {"node": node, "path": "scale"}})
        if channels:
            gltf["animations"].append({"name": aname, "channels": channels,
                                       "samplers": samplers})
            if verbose:
                print("  anim %-24s %3d/%3d tracks matched, %.2fs"
                      % (aname, used, len(af.animation.tracks),
                         af.animation.duration))
        else:
            notes.add("animation %s matched no bones and was dropped" % aname)

    if not gltf["animations"]:
        del gltf["animations"]

    gltf["accessors"] = b.accessors
    gltf["bufferViews"] = b.views
    gltf["buffers"] = [{"byteLength": len(b.blob)}]

    # ---- glb container
    js = json.dumps(gltf, separators=(",", ":")).encode()
    while len(js) % 4:
        js += b" "
    blob = bytes(b.blob)
    while len(blob) % 4:
        blob += b"\0"
    glb = (struct.pack("<3I", 0x46546C67, 2, 28 + len(js) + len(blob))
           + struct.pack("<2I", len(js), 0x4E4F534A) + js
           + struct.pack("<2I", len(blob), 0x004E4942) + blob)
    with open(out_path, "wb") as fh:
        fh.write(glb)
    if verbose:
        print("wrote %s (%.1f KB, %d meshes, %d bones, %d animations)"
              % (out_path, len(glb) / 1024.0, len(f.meshes), len(f.bones),
                 len(gltf.get("animations", []))))
        for n in sorted(notes):
            print("  note: %s" % n)
    return notes


def convert_obj(model_path, out_path):
    """Static OBJ of the bind pose, Y-up, for quick inspection."""
    f = grn.parse(model_path)
    if not f.meshes:
        raise SystemExit("%s has no meshes" % model_path)
    lines = ["# %s via grn2gltf.py" % os.path.basename(model_path)]
    base_v = base_t = base_n = 1
    for mesh in f.meshes:
        lines.append("o %s" % (mesh.name or "mesh"))
        for x, y, z in mesh.positions:
            lines.append("v %.6f %.6f %.6f" % (x, z, -y))
        for x, y, z in mesh.normals:
            lines.append("vn %.6f %.6f %.6f" % (x, z, -y))
        for u, v, _w in mesh.texcoords:
            lines.append("vt %.6f %.6f" % (u, 1.0 - v))
        for face in mesh.faces:
            refs = []
            for k in (1, 0, 2):          # winding, as in the glb path
                p = face.pos[k] + base_v
                t = ("%d" % (face.texcoord[k] + base_t)) if face.texcoord else ""
                n = face.normal[k] + base_n
                refs.append("%d/%s/%d" % (p, t, n))
            lines.append("f %s" % " ".join(refs))
        base_v += len(mesh.positions)
        base_t += len(mesh.texcoords)
        base_n += len(mesh.normals)
    with open(out_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("wrote %s" % out_path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("model", help="a .grn model file (must contain a mesh)")
    ap.add_argument("--anim", action="append", default=[],
                    help="a .grn animation file to include (repeatable)")
    ap.add_argument("-o", "--out", help="output .glb path")
    ap.add_argument("--obj", help="write a static OBJ instead/as well")
    ap.add_argument("--texture", action="append", default=[],
                    help="PNG/JPEG to bind: either PATH (all materials) or "
                         "NAME=PATH matching the material's texture object "
                         "name (repeatable); e.g. recovered from the "
                         "sibling .omt container")
    ap.add_argument("--scale", type=float, default=INCH,
                    help="root uniform scale (default %g, inches to metres)" % INCH)
    a = ap.parse_args()
    if not a.out and not a.obj:
        a.out = os.path.splitext(a.model)[0] + ".glb"
    if a.obj:
        convert_obj(a.model, a.obj)
    if a.out:
        tex = None
        if a.texture:
            tex = {}
            for t in a.texture:
                if "=" in t:
                    k, _e, v = t.partition("=")
                    tex[k] = v
                else:
                    tex["*"] = t
        convert(a.model, a.anim, a.out, texture_path=tex, scale=a.scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
