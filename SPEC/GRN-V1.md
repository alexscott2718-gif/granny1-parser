# The Granny 1 `.grn` file format

**Status: container, meshes, skeletons, skinning, materials and animations are
all read, end to end, and validated across two shipped games.**

Everything below was derived from `.grn` files themselves unless a section
says otherwise, and validated across **674 shipped files with zero failures**
(*SpongeBob SquarePants: Employee of the Month*), plus **389 of 389** files of
a second title (*Jimmy Neutron vs. Jimmy Negatron*) — 1,063 files in all.
Where a claim rests on an oracle other than the files, §13 says which.

> **Scope.** This documents a *file format* so that files can be read. It is
> not a description of RAD Game Tools' `granny.dll` — no function of that
> library is described, listed or reproduced here. See
> [`../PROCESS.md`](../PROCESS.md).

The reference reader is [`grn.py`](../grn.py), one standard-library-only
module; every structure below cites the section the parser implements.

---

## 0. Why this document exists

Granny **2** (`.gr2`) has several community parsers. Granny **1** (`.grn`) had
none, so the character meshes, skeletons and animations of every game that
shipped on Granny 1 were unreadable without RAD's runtime — which cannot be
redistributed and only runs on 32-bit Windows.

The primary corpus is the 674 `.grn` files of *SpongeBob SquarePants: Employee
of the Month* (AWE Games / THQ, 2002), exporter stamp
`RAD 3D Studio MAX 4.x / 1.2b / 10-4-2000 / win32`. The cross-title corpus is
the 389 files of *Jimmy Neutron vs. Jimmy Negatron* (AWE Games / THQ, 2002).

**The bulk payload is not compressed** — measured entropy across mesh and
animation regions is 4.9–6.0 bits/byte with about a third of the bytes zero.
**One region is:** the embedded texture (§10) opens with the FourCC `BIKi`
and is high-entropy throughout: an image compressed with RAD's own codec.
That codec is deliberately not reproduced here (see PROCESS.md); everything
else in the file is plain structured binary.
*(This corrects the earlier blanket claim that nothing in these files is
compressed — the claim was made from bulk-region entropy before the texture
block was identified.)*

## 1. File layout

```
0x00 .. 0x3f    64-byte magic -- byte-identical in all 1,063 files of BOTH titles
0x40  u32       0xca5e0000
0x44  u32       version -- 3 in every file seen
0x48  u32       crc32 of what follows (algorithm not identified; not validated)
0x4c  u32       0
0x50  u32       total size of everything after the magic == filesize - 64
0x54 .. 0x5f    zero
0x60            three section entries, 20 bytes each
```

A section entry is:

```
u32   tag         0xca5e0101 | 0xca5e0102 | 0xca5e0103
u32   0
u32   file offset of the section
u32   crc32 of the section
u32   0
```

The three always appear in this order **by tag**, but not in file order:
`0102` and `0103` come first, `0101` is the trailer at the end of the file.

| section | role |
|---|---|
| `ca5e0102` | file info — its string table carries the exporter stamp |
| `ca5e0103` | the model: objects, skeleton, meshes, materials, animation |
| `ca5e0101` | trailer; its directory is a single end marker landing exactly on EOF |

## 2. A section

```
+0x00  u32   record count
+0x04  u32   0
+0x08  u32   0x9c            constant in every file
+0x0c  u32   0
+0x10        record count * 12 bytes of records
```

Each record is:

```
u32   tag             0xca5e____
u32   offset          relative to the SECTION start, not the file start
u32   subtree size    see 3
```

**The first record of every section points exactly at the end of that
section's own directory** — i.e. at the first byte of the data area. That
identity holds in all 1,063 files and is the cheapest check that a reader is
aligned correctly.

A record's data extent is not stored. Sorting all records by `offset` and
taking the gap to the next distinct offset gives each one its extent, and
those extents tile the data area exactly. Several records commonly share one
offset; that is meaningful, not a bug — a parent node's offset is its first
child's data, the same convention as `ca5e0200` pointing at the directory end.

## 3. The directory is a tree

The third field is **not** a count of elements in the data and **not** an
index into the record array. Both readings were tested against the corpus and
both fail. It is a **subtree size**: record *i* owns records *i+1 … i+value*.

Validated: **the spans nest without a single violation in all 1,063 files** —
every span is wholly contained in or wholly disjoint from every other. An
arbitrary integer would not do that.

So a section decodes to a forest of typed nodes, each with a data pointer.
The top level of a model file:

```
ca5e0200   data-start pointer AND the string table (4.1)
ca5e0f03   the object property list (4.2)
ca5e0a01   4 bytes, meaning unknown
ca5e0b01   the node list (6)
ca5e0602   the geometry container: one ca5e0601 subtree per mesh (7)
ca5e0507   the skeleton (8)
ca5e0304   the embedded textures (10)
ca5e0d01   the material list (10)
ca5e0c01   node/bone identity and per-mesh bone bindings (6, 9)
ca5e0e01   per-mesh render data: texcoord corners, material choice (7)
ca5e1205   the animation (11)
ca5effff   end marker
```

## 4. The string table and the property list

### 4.1 The string table

The `ca5e0200` record's data is a string table:

```
u32   count
u32   byte_length
      byte_length bytes of NUL-separated strings, opening with a NUL
      so that index 0 is the empty string
```

It holds every object and bone name, and the exporter also leaves the
original authoring path in it (`D:\SpongeBob 2\Characters\SpongeBob\
SB_Model.max` — 3ds Max **Biped** rigs, consistent with the exporter stamp).

### 4.2 The `ca5e0f0x` subtree is a property list

```
ca5e0f03                     root
  ca5e0f00                   one object
    ca5e0f05                 its property list
      ca5e0f01               property NAME  -- a string index
        ca5e0f06 > ca5e0f02  property VALUE -- second u32 is a string index
      ...
```

Property names and values are **indices into the string table** and must be
resolved through it — never hardcoded. (They were first misread as member
type codes because they only ever took three values per file; the values are
simply where `__ObjectName`, `__FileName` and `__Description` sit in that
file's table. The tidy histogram was the tell.) Only those three property
names occur in either corpus.

**Validated:** across all 674 EotM files, **23,359 objects, every one with a
resolvable `__ObjectName`** — 100%, zero failures.

## 5. What is NOT established

Kept honest and in one place, because a parse that tiles a file exactly is
necessary, not sufficient:

- **The crc32 variant is unidentified**, so a writer cannot yet produce valid
  files, and the header/section checksums are carried but not verified.
- **The `BIKi` texture codec is out of scope by design** (PROCESS.md): the
  embedded texture's dimensions, depth and compressed bytes are read, the
  image is not decoded. One EotM file (`patalteat_model.grn`) carries two
  textures with depth 0 and non-`BIKi` markers — a different, unidentified
  payload form.
- **The 0102 info section's `ca5e1000..1003` records** are read structurally
  but their fields (48 bytes of floats and small ints) have no assigned
  meaning.
- **Small constants without a meaning yet:** `ca5e0a01` (4 bytes, 0);
  keyed-track header words `[1..5] = (0,1,2,2,{1,2})` and `[9..12] =
  (0,1,2,0)` — the `{1,2}` word varies per file family and is unexplained;
  sampled-track header words `[1..3] = (0,0,0)`; the third word of `ca5e0d03`.
- **The second texcoord channel's purpose** (present in 4 of 389 JN vs JN
  files, never in EotM) — the indices parse and validate, the channel's use
  (decal? lightmap?) is unknown.
- **Non-diagonal bone scale** occurs on 46 of 22,892 bones; the 3x3 is
  returned as stored, but what the runtime does with the shear has not been
  tested.
- **`ca5e0c04`/`ca5e0c09`** always share their first `ca5e0c0a` record's data;
  whether they ever carry distinct data is unobserved.

## 6. Object references and node slots

Everything in the file that names an object does it one of two ways:

**By object ordinal.** An `ca5e0f04` record's data is a single u32: a
**1-based index into the object list** (the order the `ca5e0f00` records
appear). Meshes, materials and textures carry an `0f04` giving their name.

**By node slot.** The `ca5e0b01` subtree lists the scene's *nodes*: one
`ca5e0b00` (each wrapping an `0f04`) per node, whose cell is that node's
1-based object ordinal. The **slot** is the node's 1-based position in this
list. Two structures use slots:

- `ca5e0c02` (under `0c01 > 0c00 > 0c07 > 0c08`): an array of one u32 per
  skeleton bone — **bone i lives in node slot `0c02[i]`**. It is a
  permutation of 1..nbones in every file.
- animation tracks name the node they drive by slot (§11).

So a bone's name resolves as
`objects[cells[0c02[bone] - 1] - 1].__ObjectName`, and the model file's three
objects that are not nodes (mesh objects and texture objects) are exactly the
ones absent from the cell list. Both facts hold corpus-wide; resolving tracks
through any other axis (object ordinals, string indices) scrambles limbs, and
that misread is preserved in §13 as a worked example of the method.

## 7. A mesh

The `ca5e0602` container holds one `ca5e0601` subtree per mesh:

```
ca5e0601                       one mesh
  ca5e0604 > ca5e0603          vertex pools:
    ca5e0801                   positions: npos * float[3], model space
    ca5e0802                   normals:   nnrm * float[3], unit length
    ca5e0804                   texcoords: one ca5e0803 per channel:
      ca5e0803                   u32 components (=3), then n * float[3] (u,v,w)
      [ca5e0803]                 optional second channel (JN vs JN only)
  ca5e0702                     the skin (9)
  ca5e0901                     faces: per triangle SIX u32 --
                               3 position indices then 3 normal indices
  ca5e0f04                     the mesh's object (its name)
```

The three pools are **independent**: `npos != nnrm != ntexcoords` in general
(SB_Model: 645 positions, 792 normals, 1,555 texcoords for 1,264 triangles).
A triangle's texcoord corners live separately, under `ca5e0e01`:

```
ca5e0e01 > ca5e0e00 > ca5e0e07   one ca5e0e02 group PER MESH:
  ca5e0e02                       8 bytes; second u32 = the mesh's material,
                                 1-based into the material list (10)
    ca5e0e03 wrappers            small records, roles unassigned
    ca5e0e06                     u32 ntri, then per row:
                                   u32 triangle index (into 0901's order --
                                       identity in EotM, material-sorted
                                       permutation in JN vs JN)
                                   per corner, per channel: u32 texcoord index
                                 row stride = 4 + 12 * nchannels
                                 (no channels -> rows are the index alone)
```

The `0e02` groups (and the `0c03` binding groups, §9) appear in one shared
order that does **not** always match the `0601` mesh order; the pairing is
recovered by content (triangle count, channel count, index ranges).

**Winding.** Stored corners are in the authoring (right-handed, Z-up) space;
RAD's runtime renders them mirrored, swapping corners 0 and 1. Validated
corner-by-corner against the live capture: all 3,792 corners of SB_Model
match with the swap, none without it.

**Welding.** The runtime expands per-corner (position, normal, texcoord)
triples into flat vertices (1,030 for SB_Model). A converter that welds on
the index triple reproduces the runtime's triangles exactly (§13).

## 8. The skeleton

```
ca5e0507 > ca5e0505 > ca5e0508    then nbones * ca5e0506
```

Each `ca5e0506` record is a 68-byte bone:

```
+0x00  u32       PARENT INDEX -- bone 0 is the root and stores 0
+0x04  float[3]  position     -- relative to the parent
+0x10  float[4]  orientation  -- quaternion, (x, y, z, w)
+0x20  float[9]  scale/shear  -- row-major 3x3
```

> **Correction.** The first field was previously recorded as `flags`. It is
> the parent index: it precedes the bone's own index in every one of the
> 22,892 bones across both corpora, composing transforms down the chain
> produces anatomically correct world positions, and the resolved hierarchy
> is a textbook 3ds Max Biped tree, attachments included.

The quaternion convention (x, y, z, w) is fixed by every root bone reading
`(0, 0, 0, 1)`. Scale is identity on 21,213 bones, diagonal on 1,633,
non-diagonal on 46.

## 9. Skinning

The `ca5e0702` record:

```
u32   count            == npos, or 0 for a rigid (unskinned) mesh
u32   max slot         highest skin slot used
u32   max influences   largest per-vertex influence count (1..4 observed)
per position:
    u32   influence count
    per influence:  u32 skin slot, float weight
```

Weights sum to 1 on every EotM vertex; one JN vs JN file (`Squirrelbase.grn`)
ships rows summing to 0. Positions are **model-space**, not bone-local; a
runtime (or converter) skins them through inverse bind matrices composed from
§8.

Skin slots resolve through the mesh's **binding group** — under
`ca5e0c01 > 0c00 > 0c06`, one `ca5e0c03` group per mesh, holding one
`ca5e0c0a` (32 bytes) per slot, in slot order:

```
u32       skeleton bone index
float     bounding radius      \  bone-local bounds of the
float[3]  box min              /  vertices bound to this slot
float[3]  box max
```

The binding list covers the whole slot space; a slot may exist that no vertex
references. A rigid mesh has exactly one binding: the bone it rides.
Validated geometrically: transforming each slot's vertices into its bone's
local frame lands them inside the stated box for 42/42 slots of SB_Model and
resolves the group-to-mesh pairing uniquely on multi-mesh files.

## 10. Materials

The material list lives under `ca5e0d01`: one `ca5e0d00` entry per material,
each naming itself with an `0f04` (Max material names — `1 - Default`, `SB`):

- `ca5e0d02` — an untextured colour: 4 floats, RGBA.
- `ca5e0d03` — a textured material: 12 bytes `(0, k, 1)` where **k is the
  1-based index of the embedded texture** it uses.

The embedded textures live under `ca5e0304`, one `ca5e0301` each, in `k`
order, with an `0f04` naming the texture object (whose `__FileName` property
holds the original bitmap path):

```
u32   width      u32   height      u32   depth (4)
u32   codec FourCC -- `BIKi` in 127 of 128 EotM texture files
      compressed payload, NOT decoded here (PROCESS.md)
```

A mesh selects its material through its `0e02` group's second word (§7).

## 11. Animation

The `ca5e1205` subtree (`> 1200 > {1201, 1203}` wrappers) holds one
`ca5e1204` record per track. A track drives one node, named by **node slot**
(§6) in its first word. Two forms exist, distinguished by the third word:

**Keyed** (word 2 == 1): a 13-word header
`(slot, 0, 1, 2, 2, {1|2}, npos, nrot, nscale, 0, 1, 2, 0)`, then key times
as f32 seconds — `npos` position times, `nrot` rotation times, `nscale` scale
times, each run sorted ascending — then the values: `npos * float[3]`
positions, `nrot * float[4]` quaternions (x, y, z, w), `nscale * float[9]`
3x3 scales.

**Sampled** (word 2 == 0): a 4-word header `(slot, 0, 0, 0)`, one full
16-float TRS sample `(pos[3], quat[4], scale3x3[9])`, then `(f32 dt,
16-float TRS)` blocks — times are the running dt sum (dt ~ 1/60 s
throughout the corpus).

Key values are the node's **local transform relative to its parent**,
absolute (not deltas): 57 of SB_walk's 58 tracks open exactly on their
node's bind transform, and the parent-relative topology of every node shared
between a model and its animations is identical in both files (checked for
the full SB set). Animation rigs are supersets of model rigs (Dummy helpers,
`Bip01 Footsteps`); tracks for nodes a model lacks are simply not
applicable to it.

Track sizes tile exactly under both forms across all 21,065 EotM tracks and
9,188 JN vs JN tracks.

## 12. The info section

Section `ca5e0102` carries its own small string table (`0200`) whose first
entry is the exporter stamp
(`RAD 3D Studio MAX 4.x · 1.2b · 10-4-2000 · win32 · (C) RAD Game Tools`),
plus records `ca5e1000..1003` (76 bytes total) that remain unassigned (§5).

## 13. Validation, and which oracle each claim rests on

`tools/validate.py` runs `grn.py` over a corpus and enforces everything
above; **674 of 674** EotM files and **389 of 389** JN vs JN files pass.

**Derived from the data files alone:** the container (§1–3), strings and
properties (§4), object references (§6), skeleton parents (§8 — internal
evidence: precedence, composition, Biped topology), skinning tiling (§9),
materials (§10), animation tiling and bind-match (§11).

**Checked against RAD's own runtime** (`gwatch` capture of the live game
decoding `SB_Model.grn` — see `awe-analysis/GRANNY-API.md`):

- position pool: the runtime's vertex 0 at load is the file's position 0,
  X-mirrored;
- texcoord corners: all 3,792 corners equal the runtime's, after the winding
  swap — 1,264 of 1,264 triangles;
- normals: 3,789 of 3,789 finite runtime normals equal the file's pool
  through the corner indices, X-mirrored (the other 3 are NaN in the capture,
  not in the file);
- welding: index-triple welding reproduces the runtime's triangle list
  corner-for-corner.

**A misread worth keeping** (per the method: record the wrong turns): track
node references were first resolved as object ordinals because one
high-profile track (`Bip01`, the walk bounce) happened to match under that
reading. Applied to a whole rig it produced limbs keyed with other limbs'
transforms. Scoring every candidate mapping by "does key 0 equal the named
bone's bind transform" separated them instantly: slot-resolution matched
57/58 tracks, ordinal-resolution 6/58. One good-looking match is a
hypothesis, not a mapping.
