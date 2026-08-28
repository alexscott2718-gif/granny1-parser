# granny1-parser

A standalone parser and glTF converter for **RAD Game Tools Granny 1**
(`.grn`) files — the 3D asset format behind AWE Games' 2002 Nickelodeon
titles, and the one Granny generation that had **no public parser** until
now: Granny 2 (`.gr2`) has several community readers; Granny 1 had none, so
every game that shipped on it carried unreadable meshes, skeletons and
animations unless you ran RAD's own non-redistributable, 32-bit-Windows-only
runtime.

- **[`grn.py`](grn.py)** — the parser. One file, Python 3, standard library
  only: drop it into your project and read `.grn` files. A real API first
  (objects, skeleton, meshes, skinning, materials, animations), a CLI on top.
- **[`grn2gltf.py`](grn2gltf.py)** — the converter, built on the parser:
  skinned, animated **glTF 2.0** (`.glb`) plus OBJ export, with the
  coordinate-system decisions documented rather than baked in.
- **[`SPEC/GRN-V1.md`](SPEC/GRN-V1.md)** — the format specification the
  parser is written against, claim by claim, each with the corpus count it
  was validated on and the oracle it rests on.
- **[`tools/validate.py`](tools/validate.py)** — every structural check in
  the spec, runnable over a directory of files.

## Validation

| corpus | files | result |
|---|---|---|
| *SpongeBob SquarePants: Employee of the Month* (2002) | 674 | **674 pass** |
| *Jimmy Neutron vs. Jimmy Negatron* (2002) | 389 | **389 pass** |

The mesh layer is additionally verified corner-by-corner against a live
capture of RAD's own runtime decoding the same files: positions, normals,
texture coordinates and triangle order all match exactly (SPEC §13).

## Use

```
python grn.py SB_Model.grn                     what is in the file
python grn.py SB_Model.grn --skeleton          bones, parents, names
python grn.py SB_Model.grn --objects           named objects + properties
python grn.py SB_Model.grn --tree              the raw record tree

python grn2gltf.py SB_Model.grn -o sb.glb
python grn2gltf.py SB_Model.grn --anim SB_walk.grn --anim SB_Idle1.grn -o sb.glb
python grn2gltf.py SB_Model.grn --texture sponge=sponge.png -o sb.glb
python grn2gltf.py SB_Model.grn --obj sb.obj

python tools/validate.py <directory of .grn files>
```

```python
import grn
f = grn.parse("SB_Model.grn")
f.bones[2].name                  # 'Bip01 Pelvis'
f.meshes[0].positions[0]         # (-31.4856, 16.6441, 57.9688)
f.animation.tracks[0].name       # the node a track drives
```

No game assets are included, and none are needed to use the code — bring
your own copy of a game that ships `.grn` files.

## Textures

Model files embed their textures, but compressed with RAD's own codec
(FourCC `BIKi`). This project deliberately reads the header and stops:
reproducing a proprietary codec is a materially different act from
documenting a container, and [`PROCESS.md`](PROCESS.md) draws that line on
purpose. The same art ships in the games' `.omt` containers in decodable
form (see awefan's published OMT toolkit); `grn2gltf.py --texture` binds an
image recovered that way.

## Provenance

Everything in the spec was derived from the data files by measurement, and
validated corpus-wide; no function of `granny.dll` is described, listed or
reproduced anywhere in this repository. The full reverse-engineering record
— method, wrong turns and all — lives in the sibling repository
[grn-re](https://git.exentt.com/scotty/grn-re), and a working showcase with
converted assets at <https://exentt.com/grn/>.

An Exentt Systems project — <https://exentt.com/>.
