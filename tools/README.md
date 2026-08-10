# Verification harness

These tools exist to prove lazpy decodes exactly what LASzip decodes. They are
only needed to regenerate or extend `testdata/` — running `pytest` does not
require any of them, because the reference hashes are committed.

Run them on a little-endian host. `lazpy` itself works on either (see the
byte-order note in the top-level README), and `lazdump --hash` is byte-order
independent by construction, so the committed hashes are comparable anywhere.
`mklaz` is not: it fills a LASzip point buffer, which is in host order, with
values it writes little-endian, and on a big-endian host would emit garbage
rather than fail. Nobody has needed it there, and `testdata/` is committed.

All four need a LASzip checkout built as a shared library:

```bash
git clone --depth 1 https://github.com/LASzip/LASzip.git
cmake -S LASzip -B LASzip/build -DCMAKE_BUILD_TYPE=Release
cmake --build LASzip/build -j8

LZ=LASzip
INC="-I$LZ/dll -I$LZ/include/laszip -I$LZ/build/include/laszip"
LIB="-L$LZ/build/lib -llaszip -Wl,-rpath,$PWD/$LZ/build/lib"

cc  -O2 -o lazdump lazdump.c $INC $LIB
c++ -O2 -std=c++17 -o mklaz  mklaz.cpp  -I$LZ/src $INC $LIB
c++ -O2 -std=c++17 -o mklax  mklax.cpp  -I$LZ/src $INC $LIB
```

## lazdump

Decodes a LAS/LAZ file with LASzip and reports what it got. It asks for LAS 1.4
compatibility mode, as lazpy always applies it, so a disguised 1.4 file is
reported as the file it stands in for; ordinary files are unaffected.

```bash
./lazdump cloud.laz reference.txt [max_points]   # one text line per point
./lazdump cloud.laz --hash                       # whole-file FNV-1a checksum
./lazdump cloud.laz --inside minx miny maxx maxy # a rectangle query
```

`--inside` is laszip's own `laszip_read_inside_point`, and prints an FNV-1a
hash of the *indices* of the points it selects, then how many there were. The
indices rather than the points, because which points a rectangle query selects
is the whole of what a spatial index decides; that every field of every point
decodes correctly is what `--hash` already says.

It stops at the point count the header states, which
`laszip_read_inside_point` does not: with no spatial index nothing under it
knows where the points end, so it decodes past the last one and can hand back
whatever the bytes behind it happen to say.

## mklaz

Writes a synthetic LAS/LAZ file for a given point data format and LASzip item
version. It uses LASzip's internal API rather than the public DLL, because the
DLL always picks the default item version for a point type and the whole point
is to exercise v1 and v4 as well.

```bash
./mklaz <point_type 0-10> <version 0-4> <npoints> <chunk_size> out.laz [--compat]
```

Version 0 writes uncompressed LAS. A chunk size of 0 selects LASzip's original
`POINTWISE` container -- the whole file as one stream, no chunk table -- which
only exists for point types 0-5.

`--compat` writes a LAS 1.4 point type (6-10) in LAS 1.4 compatibility mode: a
legacy file whose points carry their 1.4-only fields in extra bytes. That path
goes through the public DLL instead, since compatibility mode is the DLL's --
it builds the two records describing the disguise and folds each point into it
-- so the item version is whatever the DLL picks and only 0 and 2 are on offer.

The generated points deliberately sweep return numbers, scanner channels,
classification flags and GPS-time patterns so the rare branches of each coder
are reached. In compatibility mode they also sweep past what the legacy record
can hold -- classifications above 31, return numbers above 7, scan angles the
one-byte rank saturates on -- so the fields that travel in the extra bytes
carry something.

## mklax

Builds a LASzip spatial index for an existing file, using LASzip's own
`LASquadtree` and `LASindex`, so the fixture is the reference implementation's
work rather than lazpy's.

```bash
./mklax cloud.laz <cell_size> <minimum_points> <maximum_intervals> [--append]
```

Without `--append` it writes the sidecar `cloud.lax`. With it, the index goes
into the file itself as an extended record and the LASzip VLR is made to point
at it, which is what `lasindex -append` from LAStools does; laszip's own
`LASindex::append` is compiled out of the DLL build, so that part is written
here.

`minimum_points` and `maximum_intervals` are `LASindex::complete`'s: cells
holding fewer than `minimum_points` between them are merged into their parent,
and intervals are merged until there are no more than `maximum_intervals` --
negative meaning that many per cell. laszip's own index creation uses
`100000, -20` with a cell size of 100, which over a five-hundred-point fixture
would coarsen the whole tree into a single cell; the values below are chosen so
the fixtures have a hierarchy in them to test.

The quadtree is built over the extent of the points rather than over the
header's bounding box, which is what laszip uses. The files in `testdata/`
carry a placeholder box of a million metres each way, and a tree over that puts
every point in one cell. A file whose header is right gets the same tree either
way.

## compare_with_laszip.py

Reads the same file with lazpy and compares against a `lazdump` reference,
naming the first field that differs.

```bash
python tools/compare_with_laszip.py cloud.laz reference.txt [max_points]
python tools/compare_with_laszip.py cloud.laz --hash
```

## Regenerating testdata/

```bash
for pt in 0 1 2 3 4 5 6 7 8 9 10; do
  if [ "$pt" -le 5 ]; then vs="0 1 2"; else vs="0 3 4"; fi
  for v in $vs; do
    [ "$v" -eq 0 ] && ext=las || ext=laz
    ./mklaz "$pt" "$v" 500 137 "../testdata/pt${pt}_v${v}.$ext"
  done
done

# non-chunked variants; POINTWISE predates the 1.4 point types
for pt in 0 1 2 3 4 5; do
  ./mklaz "$pt" 1 500 0 "../testdata/pt${pt}_v1_pointwise.laz"
done

# the same 1.4 point types again, disguised as legacy ones
for pt in 6 7 8 9 10; do
  ./mklaz "$pt" 0 500 137 "../testdata/pt${pt}_compat_v0.las" --compat
  ./mklaz "$pt" 2 500 137 "../testdata/pt${pt}_compat_v2.laz" --compat
done

# spatial indexes: a sidecar for one file per container shape, and one file
# carrying its index inside itself
for f in pt0_v0.las pt1_v1_pointwise.laz pt1_v2.laz pt6_v3.laz; do
  ./mklax "../testdata/$f" 1.0 30 -20
done
cp ../testdata/pt1_v2.laz ../testdata/pt1_v2_appended.laz
./mklax ../testdata/pt1_v2_appended.laz 1.0 30 -20 --append

# the point files only -- ".la[sz]" is what leaves the indexes out
: > ../testdata/reference_hashes.txt
for f in ../testdata/pt*.la[sz]; do
  echo "$(basename "$f") $(./lazdump "$f" --hash)" >> ../testdata/reference_hashes.txt
done

# what laszip's own rectangle query selects, for the indexed files
: > ../testdata/reference_inside.txt
for f in pt0_v0.las pt1_v1_pointwise.laz pt1_v2.laz pt6_v3.laz \
         pt1_v2_appended.laz; do
  while read -r a b c d; do
    set -- $(./lazdump "../testdata/$f" --inside "$a" "$b" "$c" "$d" 2>/dev/null)
    echo "$f $a $b $c $d $2 $1" >> ../testdata/reference_inside.txt
  done <<'RECTS'
1498 1699 1500 1701
1490 1690 1510 1710
1600 1800 1601 1801
1499.5 1700.25 1499.75 1700.5
1499 1700 1501 1702
1496.62 1697.5 1498 1699
1500 1698 1501 1699
1500.5 1700.5 1501.5 1701.5
RECTS
done
```

The rectangles are in the coordinates `mklaz`'s points happen to occupy, which
are the same in every fixture: a few square metres around 1499, 1699. They
cover a rectangle inside the points, one larger than the whole indexed area,
one nowhere near it, one whose cells hold points but none inside it, one on
cell boundaries, one anchored at the extreme corner, and two small enough that
the index rules most of the file out.

`pt1_v2_appended.laz`'s reference is a filtered full scan: laszip reads only a
sidecar index, never an appended one, so it answers the question the slow way.
That the answer must be the same either way is the point.
