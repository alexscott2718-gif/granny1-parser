#!/usr/bin/env python3
"""grn.py -- a standalone parser for RAD Game Tools Granny 1 `.grn` files.

One file, standard library only. Drop it into your project and read Granny 1
files -- the format behind the character meshes, skeletons and animations of
AWE Games' 2002 Nickelodeon titles, for which no other public parser exists.

Every structure this module reads is documented in SPEC/GRN-V1.md in the
repository this file ships from (https://git.exentt.com/scotty/grn-re); section
references below are to that document.  All layout claims were validated
against the 674 shipped `.grn` files of *SpongeBob SquarePants: Employee of the
Month* (AWE Games / THQ, 2002) and the mesh layer additionally against a live
capture of RAD's own runtime decoding the same files (SPEC §7 and §8 name the
oracle per claim).

The parser READS; it does not interpret.  Positions come back in the authoring
coordinate system (3ds Max, Z-up, right-handed, model space), quaternions and
scale matrices exactly as stored.  Axis conversion and unit scaling belong in
a converter built on top (see grn2gltf.py).

Usage as a library:

    import grn
    f = grn.parse("SB_Model.grn")
    f.bones[2].name              -> 'Bip01 Pelvis'
    f.bones[2].parent            -> 1
    f.meshes[0].positions[0]     -> (-31.4856, 16.6441, 57.9688)
    f.meshes[0].faces[0]         -> Face(pos=(..), normal=(..), texcoord=(..))
    f.animation.tracks[0].name   -> 'Bip01 R Toe01'

Usage as a tool:

    python grn.py FILE.grn                summary
    python grn.py FILE.grn --tree         the record tree
    python grn.py FILE.grn --objects      named objects and properties
    python grn.py FILE.grn --skeleton     bones with names and parents
    python grn.py FILE.grn --anim         animation tracks

A file that does not match the specification raises GrnError with the file
offset and what was expected there.  Silence is how a wrong parser gets
trusted; this one fails loudly instead.
"""
from __future__ import annotations

import struct
import sys
from typing import List, Optional, Tuple

__all__ = [
    "parse", "GrnError", "GrnFile", "Section", "Record", "Object", "Bone",
    "Mesh", "Face", "BoneBinding", "Material", "Track", "Animation", "MAGIC",
]

# The 64-byte magic, byte-identical in all 674 corpus files (SPEC 1).
MAGIC = bytes.fromhex(
    "2a30390418466c66" "8d6d26239a52c17a"
    "7010e04484232632" "253c0a64f726611f"
    "253c0a64f726611f" "44682a3ba878e461"
    "5858715f0839ac1d" "7a3d217f604af637"
)


class GrnError(ValueError):
    """A structural violation, with the offset where it was found."""

    def __init__(self, offset: int, message: str):
        super().__init__("at 0x%x: %s" % (offset, message))
        self.offset = offset


class Record:
    """One 12-byte directory record (SPEC 2): tag, offset, subtree size.

    `offset` here is absolute in the file (the raw value is section-relative);
    `size` is the record's data extent, derived by tiling (SPEC 2); `children`
    are the records it owns through its subtree span (SPEC 3).
    """

    __slots__ = ("tag", "offset", "subtree", "size", "children", "index")

    def __init__(self, tag, offset, subtree, index):
        self.tag = tag
        self.offset = offset
        self.subtree = subtree
        self.index = index
        self.size = 0
        self.children: List["Record"] = []

    def find(self, tag) -> Optional["Record"]:
        for c in self.children:
            if c.tag == tag:
                return c
        return None

    def find_all(self, tag) -> List["Record"]:
        return [c for c in self.children if c.tag == tag]

    def walk(self):
        for c in self.children:
            yield c
            yield from c.walk()

    def __repr__(self):
        return "Record(ca5e%04x @0x%x sub=%d)" % (self.tag, self.offset, self.subtree)


class Section:
    """One of the three sections (SPEC 1-2)."""

    __slots__ = ("tag", "offset", "crc", "count", "records", "roots")

    def __init__(self, tag, offset, crc):
        self.tag = tag
        self.offset = offset
        self.crc = crc
        self.count = 0
        self.records: List[Record] = []
        self.roots: List[Record] = []


class Object:
    """A named object from the property list (SPEC 4.2)."""

    __slots__ = ("index", "properties")

    def __init__(self, index, properties):
        self.index = index
        self.properties = properties      # list of (name, value) string pairs

    @property
    def name(self) -> str:
        for k, v in self.properties:
            if k == "__ObjectName":
                return v
        return ""

    def __repr__(self):
        return "Object(%r)" % self.name


class Bone:
    """One skeleton node (SPEC 9): 68-byte transform plus resolved identity.

    `position`, `rotation` (x, y, z, w) and `scale` (row-major 3x3) are the
    node's transform RELATIVE TO ITS PARENT, exactly as stored.  `parent` is
    an index into the same bone array; the root is bone 0 with parent 0.
    """

    __slots__ = ("index", "parent", "position", "rotation", "scale",
                 "object_index", "name")

    def __init__(self, index, parent, position, rotation, scale):
        self.index = index
        self.parent = parent
        self.position = position
        self.rotation = rotation
        self.scale = scale
        self.object_index = -1
        self.name = ""

    def __repr__(self):
        return "Bone(%d %r parent=%d)" % (self.index, self.name, self.parent)


class Face:
    """One triangle: per-corner indices into three independent pools (SPEC 8).

    `pos` indexes Mesh.positions, `normal` indexes Mesh.normals, `texcoord`
    indexes Mesh.texcoords (None when the mesh has no texture coordinates).
    The three lists are parallel corner-by-corner.
    """

    __slots__ = ("pos", "normal", "texcoord", "texcoord2")

    def __init__(self, pos, normal, texcoord):
        self.pos = pos
        self.normal = normal
        self.texcoord = texcoord
        self.texcoord2 = None


class BoneBinding:
    """One bound bone of a mesh (SPEC 10): which skeleton bone a skin slot
    refers to, with its stated bounding sphere radius and box (bone-local)."""

    __slots__ = ("bone", "radius", "box_min", "box_max")

    def __init__(self, bone, radius, box_min, box_max):
        self.bone = bone
        self.radius = radius
        self.box_min = box_min
        self.box_max = box_max


class Mesh:
    """One mesh (SPEC 8, 10): vertex pools, faces, skin and material."""

    __slots__ = ("name", "object_index", "positions", "normals", "texcoords",
                 "texcoords2", "faces", "influences", "bindings",
                 "material_index")

    def __init__(self):
        self.name = ""
        self.object_index = -1
        self.material_index = -1          # into GrnFile.materials, -1 if none
        self.positions: List[Tuple[float, float, float]] = []
        self.normals: List[Tuple[float, float, float]] = []
        self.texcoords: List[Tuple[float, float, float]] = []
        self.texcoords2: List[Tuple[float, float, float]] = []
        self.faces: List[Face] = []
        # per position: [(skin slot, weight), ...]; empty list = rigid mesh
        self.influences: List[List[Tuple[int, float]]] = []
        # skin slot -> skeleton bone (+ bounds); rigid meshes have exactly one
        self.bindings: List[BoneBinding] = []

    @property
    def skinned(self) -> bool:
        return bool(self.influences)


class Material:
    """One material from the 0d material list (SPEC 11).

    kind == 'color':   `color` is the stored RGBA.
    kind == 'texture': the texture is an image EMBEDDED in the file,
    compressed with RAD's own codec (magic `BIKi`); its bytes are carried,
    NOT decoded -- see PROCESS.md for why this project deliberately stops at
    that boundary.  `texture_name` is the texture object's name, whose
    `__FileName` property usually holds the original bitmap path."""

    __slots__ = ("name", "kind", "color", "width", "height", "depth",
                 "codec", "payload", "texture_name")

    def __init__(self, name, kind):
        self.name = name
        self.kind = kind
        self.color = None             # (r, g, b, a) floats for kind 'color'
        self.width = 0
        self.height = 0
        self.depth = 0
        self.codec = b""              # 4 raw bytes, b'BIKi' in 127/128 files
        self.payload = b""            # compressed bytes, exactly as stored
        self.texture_name = ""


class Track:
    """One animation track (SPEC 12): key times in seconds, values exactly as
    stored -- positions vec3, rotations quaternion (x, y, z, w), scales 3x3."""

    __slots__ = ("node_slot", "object_index", "name", "header",
                 "pos_times", "positions", "rot_times", "rotations",
                 "scale_times", "scales")

    def __init__(self):
        self.node_slot = 0
        self.object_index = -1
        self.name = ""
        self.header = ()
        self.pos_times: List[float] = []
        self.positions: List[Tuple[float, float, float]] = []
        self.rot_times: List[float] = []
        self.rotations: List[Tuple[float, float, float, float]] = []
        self.scale_times: List[float] = []
        self.scales: List[Tuple[float, ...]] = []

    @property
    def duration(self) -> float:
        ts = [t[-1] for t in (self.pos_times, self.rot_times, self.scale_times) if t]
        return max(ts) if ts else 0.0


class Animation:
    __slots__ = ("tracks",)

    def __init__(self):
        self.tracks: List[Track] = []

    @property
    def duration(self) -> float:
        return max((t.duration for t in self.tracks), default=0.0)


class GrnFile:
    """A parsed `.grn` file."""

    __slots__ = ("path", "version", "crc", "sections", "exporter",
                 "strings", "objects", "bones", "meshes", "materials",
                 "animation")

    def __init__(self):
        self.path = ""
        self.version = 0
        self.crc = 0
        self.sections: List[Section] = []
        self.exporter = ""
        self.strings: List[str] = []
        self.objects: List[Object] = []
        self.bones: List[Bone] = []
        self.meshes: List[Mesh] = []
        self.materials: List[Material] = []
        self.animation = Animation()

    def object_name(self, ordinal_1based: int) -> str:
        """Resolve a 1-based object ordinal (the convention every object
        reference in the file uses -- SPEC 6) to its name."""
        if not 1 <= ordinal_1based <= len(self.objects):
            raise GrnError(0, "object ordinal %d out of range 1..%d"
                           % (ordinal_1based, len(self.objects)))
        return self.objects[ordinal_1based - 1].name


# ---------------------------------------------------------------------------
# container layer (SPEC 1-3)

def _u32s(data, offset, n):
    return struct.unpack_from("<%dI" % n, data, offset)


def _f32s(data, offset, n):
    return struct.unpack_from("<%df" % n, data, offset)


def _parse_container(data, strict):
    n = len(data)
    if n < 0x60 + 3 * 20:
        raise GrnError(0, "file is %d bytes, smaller than any valid .grn" % n)
    if data[:16] != MAGIC[:16]:
        raise GrnError(0, "not a .grn file (first 16 magic bytes differ)")
    if strict and data[:64] != MAGIC:
        diff = next(i for i in range(64) if data[i] != MAGIC[i])
        raise GrnError(diff, "magic differs from the corpus constant at byte "
                             "%d (parse with strict=False to accept)" % diff)

    marker, version, crc, zero, total = _u32s(data, 0x40, 5)
    if marker != 0xCA5E0000:
        raise GrnError(0x40, "expected header marker ca5e0000, got %08x" % marker)
    if total != n - 64:
        raise GrnError(0x50, "size-after-magic %d != filesize-64 %d" % (total, n - 64))

    sections = []
    for i in range(3):
        base = 0x60 + i * 20
        tag, z1, off, scrc, z2 = _u32s(data, base, 5)
        if tag not in (0xCA5E0101, 0xCA5E0102, 0xCA5E0103):
            raise GrnError(base, "unknown section tag %08x" % tag)
        if off >= n:
            raise GrnError(base + 8, "section offset 0x%x beyond EOF" % off)
        sections.append(Section(tag & 0xFFFF, off, scrc))
    if [s.tag for s in sections] != [0x0102, 0x0103, 0x0101]:
        raise GrnError(0x60, "sections not in the expected 0102/0103/0101 order")

    for s in sections:
        _parse_directory(data, s)
    return version, crc, sections


def _parse_directory(data, s):
    base = s.offset
    count, z1, hdr, z2 = _u32s(data, base, 4)
    if count == 0 or count * 12 > len(data):
        raise GrnError(base, "implausible record count %d" % count)
    s.count = count
    dir_end = base + 16 + count * 12
    recs = []
    for i in range(count):
        tag, rel, sub = _u32s(data, base + 16 + i * 12, 3)
        if (tag >> 16) != 0xCA5E:
            raise GrnError(base + 16 + i * 12, "record tag %08x lacks the ca5e marker" % tag)
        off = base + rel
        if off > len(data):
            raise GrnError(base + 16 + i * 12, "record offset 0x%x beyond EOF" % off)
        recs.append(Record(tag & 0xFFFF, off, sub, i))
    if recs[0].offset != dir_end:
        raise GrnError(base + 16, "first record points at 0x%x, directory ends at 0x%x"
                                  % (recs[0].offset, dir_end))

    # extents: sort by offset, gap to the next distinct offset (SPEC 2)
    bounds = sorted({r.offset for r in recs})
    end_of = {o: (bounds[i + 1] if i + 1 < len(bounds) else len(data))
              for i, o in enumerate(bounds)}
    for r in recs:
        r.size = end_of[r.offset] - r.offset

    # subtree spans build the tree; spans must nest (SPEC 3)
    stack = [(None, len(recs))]
    roots = []
    for i, r in enumerate(recs):
        while i >= stack[-1][1]:
            stack.pop()
        parent = stack[-1][0]
        if parent is None:
            roots.append(r)
        else:
            parent.children.append(r)
        if r.subtree:
            span_end = i + 1 + r.subtree
            if span_end > stack[-1][1]:
                raise GrnError(base + 16 + i * 12,
                               "subtree span %d..%d escapes its parent (..%d)"
                               % (i, span_end, stack[-1][1]))
            stack.append((r, span_end))
    s.records = recs
    s.roots = roots


# ---------------------------------------------------------------------------
# semantic layer

def _string_table(data, rec):
    count, nbytes = _u32s(data, rec.offset, 2)
    if 8 + nbytes > rec.size:
        raise GrnError(rec.offset, "string table wants %d bytes, record has %d"
                                   % (8 + nbytes, rec.size))
    blob = data[rec.offset + 8: rec.offset + 8 + nbytes]
    return [p.decode("cp1252", "replace") for p in blob.split(b"\0")]


def _parse_objects(data, roots, strings):
    objs = []
    for root in roots:
        if root.tag != 0x0F03:
            continue
        for i, node in enumerate(root.find_all(0x0F00)):
            props = []
            plist = node.find(0x0F05)
            for p in (plist.children if plist else []):
                if p.tag != 0x0F01:
                    continue
                name_i = _u32s(data, p.offset, 1)[0]
                val_i = 0
                wrap = p.find(0x0F06)
                pv = wrap.find(0x0F02) if wrap else None
                if pv is not None:
                    val_i = _u32s(data, pv.offset, 2)[1]
                for idx in (name_i, val_i):
                    if idx >= len(strings):
                        raise GrnError(p.offset, "string index %d beyond table (%d)"
                                                 % (idx, len(strings)))
                props.append((strings[name_i], strings[val_i]))
            objs.append(Object(i, props))
    return objs


def _object_ref(data, rec, nobjects):
    """An 0f04 record: a 1-based object ordinal (SPEC 6)."""
    v = _u32s(data, rec.offset, 1)[0]
    if not 1 <= v <= nobjects:
        raise GrnError(rec.offset, "object reference %d out of range 1..%d"
                                   % (v, nobjects))
    return v


def _parse_skeleton(data, roots, objects):
    bones = []
    node_cells = []       # 0b00 order: 1-based object ordinal per node slot
    for root in roots:
        if root.tag == 0x0B01:
            for nb in root.find_all(0x0B00):
                node_cells.append(_object_ref(data, nb, len(objects)))
        if root.tag == 0x0507:
            recs = [r for r in root.walk() if r.tag == 0x0506]
            for i, r in enumerate(recs):
                if r.size < 68:
                    raise GrnError(r.offset, "bone record is %d bytes, needs 68" % r.size)
                parent = _u32s(data, r.offset, 1)[0]
                pos = _f32s(data, r.offset + 4, 3)
                rot = _f32s(data, r.offset + 16, 4)
                scale = _f32s(data, r.offset + 32, 9)
                if i == 0 and parent != 0:
                    raise GrnError(r.offset, "root bone parent is %d, expected 0" % parent)
                if i > 0 and parent >= i:
                    raise GrnError(r.offset, "bone %d parent %d does not precede it"
                                             % (i, parent))
                bones.append(Bone(i, parent, pos, rot, scale))
    # identity: bone i -> node slot 0c02[i] (1-based) -> 0b00 cell -> object
    slot_to_bone = {}
    perm = None
    for root in roots:
        if root.tag == 0x0C01:
            for r in root.walk():
                if r.tag == 0x0C02:
                    if r.size < len(bones) * 4:
                        raise GrnError(r.offset, "0c02 has %d bytes for %d bones"
                                                 % (r.size, len(bones)))
                    perm = _u32s(data, r.offset, len(bones))
    if bones and perm and node_cells:
        if sorted(perm) != list(range(1, len(bones) + 1)):
            raise GrnError(0, "0c02 is not a permutation of 1..%d" % len(bones))
        if len(node_cells) != len(bones):
            raise GrnError(0, "%d node cells for %d bones" % (len(node_cells), len(bones)))
        for b in bones:
            b.object_index = node_cells[perm[b.index] - 1] - 1
            b.name = objects[b.object_index].name
            slot_to_bone[perm[b.index]] = b.index
    return bones, slot_to_bone


def _parse_meshes(data, roots, objects):
    meshes = []
    for root in roots:
        if root.tag != 0x0602:
            continue
        for mrec in root.find_all(0x0601):
            m = Mesh()
            wrap4 = mrec.find(0x0604)
            wrap3 = wrap4.find(0x0603) if wrap4 else None
            if wrap3 is None:
                raise GrnError(mrec.offset, "mesh without an 0604/0603 vertex block")
            p = wrap3.find(0x0801)
            if p is None:
                raise GrnError(wrap3.offset, "mesh without an 0801 position array")
            if p.size % 12:
                raise GrnError(p.offset, "position array size %d not /12" % p.size)
            npos = p.size // 12
            m.positions = [_f32s(data, p.offset + i * 12, 3) for i in range(npos)]
            nr = wrap3.find(0x0802)
            if nr is not None:
                if nr.size % 12:
                    raise GrnError(nr.offset, "normal array size %d not /12" % nr.size)
                m.normals = [_f32s(data, nr.offset + i * 12, 3)
                             for i in range(nr.size // 12)]
            uvwrap = wrap3.find(0x0804)
            channels = uvwrap.find_all(0x0803) if uvwrap else []
            if len(channels) > 2:
                raise GrnError(uvwrap.offset, "%d texcoord channels; at most 2 "
                                              "were ever observed" % len(channels))
            for ci, uvr in enumerate(channels):
                comp = _u32s(data, uvr.offset, 1)[0]
                if comp != 3:
                    raise GrnError(uvr.offset, "texcoord component count %d, only 3 "
                                               "observed in the corpus" % comp)
                if (uvr.size - 4) % 12:
                    raise GrnError(uvr.offset, "texcoord array size %d-4 not /12" % uvr.size)
                vals = [_f32s(data, uvr.offset + 4 + i * 12, 3)
                        for i in range((uvr.size - 4) // 12)]
                if ci == 0:
                    m.texcoords = vals
                else:
                    m.texcoords2 = vals

            sk = mrec.find(0x0702)
            if sk is None:
                raise GrnError(mrec.offset, "mesh without an 0702 skin record")
            cnt, maxslot, maxinf = _u32s(data, sk.offset, 3)
            if cnt:
                if cnt != npos:
                    raise GrnError(sk.offset, "skin covers %d positions, mesh has %d"
                                              % (cnt, npos))
                o = sk.offset + 12
                seen_max = 0
                for i in range(cnt):
                    ninf = _u32s(data, o, 1)[0]
                    if not 1 <= ninf <= maxinf:
                        raise GrnError(o, "vertex %d has %d influences, header "
                                          "allows 1..%d" % (i, ninf, maxinf))
                    seen_max = max(seen_max, ninf)
                    row = []
                    for _k in range(ninf):
                        slot, weight = struct.unpack_from("<If", data, o + 4)
                        if slot > maxslot:
                            raise GrnError(o + 4, "skin slot %d > header max %d"
                                                  % (slot, maxslot))
                        row.append((slot, weight))
                        o += 8
                    o += 4
                    # weights are returned exactly as stored; they sum to 1 on
                    # every EotM vertex, but one JN vs JN file ships rows that
                    # sum to 0, so judging them is a validator's job, not ours
                    m.influences.append(row)
                if o != sk.offset + sk.size:
                    raise GrnError(o, "skin rows end at 0x%x, record at 0x%x"
                                      % (o, sk.offset + sk.size))
                if seen_max != maxinf:
                    raise GrnError(sk.offset, "header says max %d influences, "
                                              "largest row has %d" % (maxinf, seen_max))

            fr = mrec.find(0x0901)
            if fr is None:
                raise GrnError(mrec.offset, "mesh without an 0901 face list")
            if fr.size % 24:
                raise GrnError(fr.offset, "face list size %d not /24" % fr.size)
            ntri = fr.size // 24
            q = _u32s(data, fr.offset, ntri * 6)
            nnrm = len(m.normals)
            for t in range(ntri):
                pi = q[6 * t: 6 * t + 3]
                ni = q[6 * t + 3: 6 * t + 6]
                if max(pi) >= npos:
                    raise GrnError(fr.offset + t * 24, "position index %d >= %d"
                                                       % (max(pi), npos))
                if nnrm and max(ni) >= nnrm:
                    raise GrnError(fr.offset + t * 24 + 12, "normal index %d >= %d"
                                                            % (max(ni), nnrm))
                m.faces.append(Face(tuple(pi), tuple(ni), None))

            oref = mrec.find(0x0F04)
            if oref is not None:
                m.object_index = _object_ref(data, oref, len(objects)) - 1
                m.name = objects[m.object_index].name
            meshes.append(m)
    return meshes


def _parse_materials(data, roots, objects):
    """The 0d01 subtree is the material list; each 0d00 entry is either a
    plain colour (0d02: 4 floats RGBA) or a textured material (0d03, whose
    second word is a 1-based index into the 0301 embedded textures).  The
    0304 subtree carries those textures, in that index order (SPEC 11)."""
    textures = []
    for root in roots:
        if root.tag != 0x0304:
            continue
        for mrec in [r for r in root.walk() if r.tag == 0x0301]:
            w, h, depth = _u32s(data, mrec.offset, 3)
            codec = data[mrec.offset + 12: mrec.offset + 16]
            payload = data[mrec.offset + 16: mrec.offset + mrec.size]
            name = ""
            oref = mrec.find(0x0F04)
            if oref is not None:
                name = objects[_object_ref(data, oref, len(objects)) - 1].name
            textures.append((w, h, depth, codec, payload, name))

    mats = []
    ntex_refs = 0
    for root in roots:
        if root.tag != 0x0D01:
            continue
        for entry in root.find_all(0x0D00):
            name = ""
            oref = None
            for r in entry.walk():
                if r.tag == 0x0F04:
                    oref = r
            if oref is not None:
                name = objects[_object_ref(data, oref, len(objects)) - 1].name
            col = entry.find(0x0D02)
            tex = entry.find(0x0D03)
            if col is not None:
                m = Material(name, "color")
                if col.size < 16:
                    raise GrnError(col.offset, "colour material is %d bytes, "
                                               "needs 16" % col.size)
                m.color = _f32s(data, col.offset, 4)
            elif tex is not None:
                m = Material(name, "texture")
                _z, k, _one = _u32s(data, tex.offset, 3)
                if not 1 <= k <= len(textures):
                    raise GrnError(tex.offset, "material references texture %d "
                                               "of %d" % (k, len(textures)))
                ntex_refs += 1
                (m.width, m.height, m.depth, m.codec, m.payload,
                 m.texture_name) = textures[k - 1]
            else:
                continue
            mats.append(m)
    if ntex_refs != len(textures):
        raise GrnError(0, "%d embedded textures but %d textured materials"
                          % (len(textures), ntex_refs))
    return mats


def _attach_render_groups(data, roots, meshes, bones, materials):
    """The 0e01 subtree holds one 0e02 group per mesh (texcoord corner
    indices in its 0e06, and the mesh's 1-based material index), and the
    0c01 subtree one 0c03 group per mesh (its bound bones), both in ONE
    shared order that does not always match the 0601 mesh order.  The pairing
    is recovered by content: triangle count, texcoord presence, and texcoord
    index range identify each group's mesh (SPEC 8, 10)."""
    egroups = []
    for root in roots:
        if root.tag == 0x0E01:
            for r in root.walk():
                if r.tag == 0x0E02:
                    egroups.append(r)
    cgroups = []
    for root in roots:
        if root.tag == 0x0C01:
            for r in root.walk():
                if r.tag == 0x0C03:
                    cgroups.append(r)
    if not meshes:
        return
    if len(egroups) != len(meshes) or len(cgroups) != len(meshes):
        raise GrnError(0, "%d render groups / %d binding groups for %d meshes"
                          % (len(egroups), len(cgroups), len(meshes)))

    # decode each 0e02 group once
    decoded = []
    for g in egroups:
        e = None
        for r in g.walk():
            if r.tag == 0x0E06:
                e = r
        if e is None:
            raise GrnError(g.offset, "render group without an 0e06 face list")
        ntri = _u32s(data, e.offset, 1)[0]
        if ntri == 0:
            raise GrnError(e.offset, "0e06 with zero triangles")
        stride = None
        for nch in (2, 1, 0):            # stride = 4 + 12 * channels
            want = 4 + 12 * nch
            if e.size >= 4 + ntri * want and (e.size - 4) // ntri >= want:
                stride = want
                break
        if stride is None:
            raise GrnError(e.offset, "0e06 is %d bytes for %d triangles"
                                     % (e.size, ntri))
        rows = [_u32s(data, e.offset + 4 + t * stride, stride // 4)
                for t in range(ntri)]
        # row[0] is the 0901 triangle the row belongs to: the identity order
        # in EotM, a material-sorted permutation in JN vs JN
        if sorted(r[0] for r in rows) != list(range(ntri)):
            raise GrnError(e.offset, "0e06 triangle numbers are not a "
                                     "permutation of 0..%d" % (ntri - 1))
        mat_1b = _u32s(data, g.offset, 2)[1]
        decoded.append((e, ntri, stride, rows, mat_1b))

    def fits(mesh, dec):
        _e, ntri, stride, rows, _mat = dec
        if ntri != len(mesh.faces):
            return False
        nch = (stride - 4) // 12
        have = (1 if mesh.texcoords else 0) + (1 if mesh.texcoords2 else 0)
        if nch != have:
            return False
        for ci, pool in ((0, mesh.texcoords), (1, mesh.texcoords2)):
            if ci >= nch:
                break
            n = len(pool)
            for r in rows:
                if max(r[1 + k * nch + ci] for k in range(3)) >= n:
                    return False
        return True

    # assign greedily by uniqueness; identical twins fall back to list order
    assign = [None] * len(meshes)
    remaining = set(range(len(meshes)))
    changed = True
    while changed and remaining:
        changed = False
        for gi in range(len(decoded)):
            if gi in [a for a in assign if a is not None]:
                continue
            cands = [mi for mi in remaining if fits(meshes[mi], decoded[gi])]
            if len(cands) == 1 and assign[cands[0]] is None:
                assign[cands[0]] = gi
                remaining.discard(cands[0])
                changed = True
    for mi in sorted(remaining):
        taken = {a for a in assign if a is not None}
        gi = next((g for g in range(len(decoded))
                   if g not in taken and fits(meshes[mi], decoded[g])), None)
        if gi is None:
            raise GrnError(0, "no render group matches mesh %r" % meshes[mi].name)
        assign[mi] = gi

    for mi, m in enumerate(meshes):
        gi = assign[mi]
        e, ntri, stride, rows, mat_1b = decoded[gi]
        nch = (stride - 4) // 12
        for row in rows:
            face = m.faces[row[0]]
            if nch >= 1:
                face.texcoord = tuple(row[1 + k * nch] for k in range(3))
            if nch == 2:
                face.texcoord2 = tuple(row[2 + k * nch] for k in range(3))
        if materials:
            if not 1 <= mat_1b <= len(materials):
                raise GrnError(egroups[gi].offset,
                               "mesh material %d of %d" % (mat_1b, len(materials)))
            m.material_index = mat_1b - 1

        # the 0c03 list shares the 0e02 list's order
        g = cgroups[gi]
        for r in g.walk():
            if r.tag == 0x0C0A:
                vals = struct.unpack_from("<I7f", data, r.offset)
                bone = vals[0]
                if bones and bone >= len(bones):
                    raise GrnError(r.offset, "binding names bone %d of %d"
                                             % (bone, len(bones)))
                m.bindings.append(BoneBinding(bone, vals[1], vals[2:5], vals[5:8]))
        if m.influences:
            # the binding list covers the whole slot space 0..max; a slot may
            # exist without any vertex referencing it
            slots = {s for row in m.influences for s, _w in row}
            if max(slots) >= len(m.bindings):
                raise GrnError(g.offset, "skin slot %d but only %d bone bindings"
                                         % (max(slots), len(m.bindings)))


def _parse_animation(data, roots, objects, bones, slot_to_bone):
    anim = Animation()
    for root in roots:
        if root.tag != 0x1205:
            continue
        for r in root.walk():
            if r.tag != 0x1204:
                continue
            t = Track()
            h = _u32s(data, r.offset, 13)
            # the track names its node by SLOT -- the same 1-based slot space
            # 0c02 maps bones into (SPEC 12); resolving it through the wrong
            # axis (object ordinals) scrambles limbs, which is how this was
            # found: 57/58 slot-resolved tracks open exactly on their bone's
            # bind transform, 6/58 object-resolved ones do
            t.node_slot = h[0]
            if t.node_slot not in slot_to_bone:
                raise GrnError(r.offset, "track node slot %d has no bone" % h[0])
            bone = bones[slot_to_bone[t.node_slot]]
            t.object_index = bone.object_index
            t.name = bone.name

            if h[2] == 0:
                # sampled form (SPEC 11): 4-word header, one full TRS at
                # t=0, then (time, TRS) blocks -- the leading float is the
                # sample's ABSOLUTE time in seconds (~60 Hz spacing), not a
                # delta: read as deltas, a 2 s climb becomes 115 s
                t.header = h[:4]
                nw = r.size // 4
                if (nw - 4 - 16) % 17:
                    raise GrnError(r.offset, "sampled track is %d words, not "
                                             "4 + 16 + k*17" % nw)
                nsamp = 1 + (nw - 4 - 16) // 17
                o = r.offset + 16
                tm = 0.0
                for i in range(nsamp):
                    if i:
                        tm = _f32s(data, o, 1)[0]
                        o += 4
                    vals = _f32s(data, o, 16)
                    o += 64
                    t.pos_times.append(tm)
                    t.positions.append(vals[0:3])
                    t.rot_times.append(tm)
                    t.rotations.append(vals[3:7])
                    t.scale_times.append(tm)
                    t.scales.append(vals[7:16])
                if any(t.pos_times[i] >= t.pos_times[i + 1]
                       for i in range(len(t.pos_times) - 1)):
                    raise GrnError(r.offset, "sampled track times not "
                                             "strictly ascending")
                anim.tracks.append(t)
                continue

            if h[1:5] != (0, 1, 2, 2):
                raise GrnError(r.offset, "keyed track header opens %s, expected "
                                         "(0, 1, 2, 2)" % (h[1:5],))
            t.header = h
            npk, nrk, nsk = h[6], h[7], h[8]
            want = 52 + (npk + nrk + nsk) * 4 + npk * 12 + nrk * 16 + nsk * 36
            if r.size < want:
                raise GrnError(r.offset, "track is %d bytes, key counts %d/%d/%d "
                                         "need %d" % (r.size, npk, nrk, nsk, want))
            tb = r.offset + 52
            times = _f32s(data, tb, npk + nrk + nsk)
            t.pos_times = list(times[:npk])
            t.rot_times = list(times[npk:npk + nrk])
            t.scale_times = list(times[npk + nrk:])
            for seq, label in ((t.pos_times, "position"), (t.rot_times, "rotation"),
                               (t.scale_times, "scale")):
                if any(seq[i] > seq[i + 1] for i in range(len(seq) - 1)):
                    raise GrnError(tb, "%s key times not sorted" % label)
            vb = tb + (npk + nrk + nsk) * 4
            t.positions = [_f32s(data, vb + i * 12, 3) for i in range(npk)]
            vb += npk * 12
            t.rotations = [_f32s(data, vb + i * 16, 4) for i in range(nrk)]
            vb += nrk * 16
            t.scales = [_f32s(data, vb + i * 36, 9) for i in range(nsk)]
            anim.tracks.append(t)
    return anim


def parse(source, strict=True) -> GrnFile:
    """Parse a `.grn` file from a path or a bytes object.

    strict=True (the default) also insists on the full 64-byte magic the
    corpus carries; strict=False accepts any file whose first 16 magic bytes
    match, for probing files from other Granny 1 titles.
    """
    if isinstance(source, (bytes, bytearray)):
        data, path = bytes(source), "<bytes>"
    else:
        with open(source, "rb") as fh:
            data = fh.read()
        path = str(source)

    f = GrnFile()
    f.path = path
    f.version, f.crc, f.sections = _parse_container(data, strict)

    # section 0102: file info -- the exporter stamp lives in its string table
    info = f.sections[0]
    for r in info.records:
        if r.tag == 0x0200:
            for s in _string_table(data, r):
                if s:
                    f.exporter = s
                    break

    # section 0103: the model
    model = f.sections[1]
    table = next((r for r in model.records if r.tag == 0x0200), None)
    if table is None:
        raise GrnError(model.offset, "model section has no 0200 string table")
    f.strings = _string_table(data, table)
    f.objects = _parse_objects(data, model.roots, f.strings)
    f.bones, slot_to_bone = _parse_skeleton(data, model.roots, f.objects)
    f.meshes = _parse_meshes(data, model.roots, f.objects)
    f.materials = _parse_materials(data, model.roots, f.objects)
    _attach_render_groups(data, model.roots, f.meshes, f.bones, f.materials)
    f.animation = _parse_animation(data, model.roots, f.objects, f.bones,
                                   slot_to_bone)
    return f


# ---------------------------------------------------------------------------
# CLI

def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(
        description="Inspect a Granny 1 .grn file.")
    ap.add_argument("file")
    ap.add_argument("--tree", action="store_true", help="dump the record tree")
    ap.add_argument("--objects", action="store_true", help="named objects")
    ap.add_argument("--skeleton", action="store_true", help="bones with parents")
    ap.add_argument("--anim", action="store_true", help="animation tracks")
    ap.add_argument("--lax", action="store_true",
                    help="accept a magic that matches only in its first 16 bytes")
    a = ap.parse_args(argv)

    f = parse(a.file, strict=not a.lax)
    if a.tree:
        def show(rec, depth):
            print("%s[%3d] ca5e%04x @0x%06x %8dB" % (
                "  " * depth, rec.index, rec.tag, rec.offset, rec.size))
            for c in rec.children:
                show(c, depth + 1)
        for s in f.sections:
            print("section ca5e%04x @0x%x, %d records" % (s.tag, s.offset, s.count))
            for r in s.roots:
                show(r, 1)
        return 0
    if a.objects:
        for o in f.objects:
            print("[%3d] %s" % (o.index, o.name))
            for k, v in o.properties:
                if k != "__ObjectName" and v:
                    print("       %s = %r" % (k, v))
        return 0
    if a.skeleton:
        for b in f.bones:
            print("bone %2d  parent %2d  %s" % (b.index, b.parent, b.name))
        return 0
    if a.anim:
        for t in f.animation.tracks:
            print("%-22s %3d pos keys, %3d rot keys, %3d scale keys, %.2fs"
                  % (t.name, len(t.positions), len(t.rotations),
                     len(t.scales), t.duration))
        return 0

    print(f.path)
    print("  exporter : %s" % f.exporter)
    print("  objects  : %d" % len(f.objects))
    print("  bones    : %d" % len(f.bones))
    for m in f.meshes:
        uv = ", %d texcoords" % len(m.texcoords) if m.texcoords else ", no texcoords"
        skin = "skinned to %d bones" % len(m.bindings) if m.skinned \
            else "rigid (bone %d)" % (m.bindings[0].bone if m.bindings else -1)
        print("  mesh     : %r  %d positions, %d normals%s, %d triangles, %s"
              % (m.name, len(m.positions), len(m.normals), uv, len(m.faces), skin))
    for mt in f.materials:
        if mt.kind == "color":
            print("  material : %r  colour (%.2f, %.2f, %.2f, %.2f)"
                  % ((mt.name,) + tuple(mt.color)))
        else:
            print("  material : %r  texture %r %dx%d depth %d, codec %s, "
                  "%d bytes (not decoded)"
                  % (mt.name, mt.texture_name, mt.width, mt.height, mt.depth,
                     mt.codec, len(mt.payload)))
    if f.animation.tracks:
        print("  animation: %d tracks, %.2fs" % (len(f.animation.tracks),
                                                 f.animation.duration))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
