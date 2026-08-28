# Scope and method

## What this project documents, and what it does not

**It documents a file format.** `.grn` is a container for mesh, skeleton and animation data. The
goal is to read those files without RAD's runtime, which cannot be redistributed and runs only on
32-bit Windows.

**It does not document `granny.dll`.** No function of that library is described, listed, decompiled
or reproduced in this repository. That is a commercial product belonging to RAD Game Tools, and
documenting its internals is not the goal and would not serve the goal.

The distinction matters and is worth stating rather than assuming: a program that reads a format is
not a copy of the program that wrote it, any more than a `.zip` reader is a copy of PKZIP.

### The line in practice

Everything in [`SPEC/GRN-V1.md`](SPEC/GRN-V1.md) was derived from **the data files**, by measuring
them. The library was not consulted for any claim on that page. If a future finding does require
looking at the library to understand a layout, what crosses into this repository is the **layout** —
offsets, sizes, field meanings — never code, never algorithms, never structure of the library
itself.

One thing is flagged in advance rather than discovered awkwardly later: if part of the payload turns
out to be produced by a proprietary **compression codec**, reproducing that codec is a materially
different proposition from describing a container layout, and this project will stop and say so
rather than quietly implement it.

**The case arose, and this is the stop.** The texture embedded in each model file (SPEC §10) opens
with the FourCC `BIKi` — RAD's own image codec — and is high-entropy throughout. The parser reads
the texture's dimensions, depth and compressed bytes and goes no further: no decompressor is
implemented, described or reverse engineered here. Everything else in the file remains plain
structured binary (4.9–6.0 bits/byte with about a third of all bytes zero), exactly as the original
entropy measurement said — that measurement was over the bulk regions and predates the texture
block's identification, which is why the spec no longer claims "nothing is compressed". The same
art is recoverable without touching the codec: it ships alongside in `.omt` containers whose format
is already publicly specified.

### Assets

No game assets are in this repository, and none will be. You need your own copy of a game that ships
`.grn` files.

## Method

Three habits, all of which have already caught something in this work:

**Validate across the corpus, not one file.** Every structural claim is stated together with the
number of files it holds for. 674 of 674 means something; one file means it is a hypothesis.

**A parse that tiles a file exactly is necessary, not sufficient.** A reader can consume every byte
and be wrong about all of them. The spec keeps a standing section — §5 — for what is *not*
established, and that section is meant to stay long.

**Test the competing reading, not just the favoured one.** The third field of a record was first
read as an element count, and that reading survived a check across 120 files. It was still wrong.
The competing reading — an index into the record array — was also wrong. It is a subtree size, and
that only became clear because both alternatives were tested to destruction rather than one being
confirmed. The nesting check that settled it (674 files, zero violations) is a property an arbitrary
integer could not have.

**Scan naively on purpose.** The tag census walks the file four bytes at a time looking for a marker
rather than following the structure. A structure-following scan that is wrong produces a confident,
wrong answer; a naive one produces visible noise. The five junk tags it reports, appearing 1–4 times
each inside float data, are the noise floor doing its job.
