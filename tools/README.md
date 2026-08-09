# Verification harness

These tools exist to prove lazpy decodes exactly what LASzip decodes. They are
only needed to regenerate or extend `testdata/` — running `pytest` does not
require any of them, because the reference hashes are committed.

All three need a LASzip checkout built as a shared library:

```bash
git clone --depth 1 https://github.com/LASzip/LASzip.git
cmake -S LASzip -B LASzip/build -DCMAKE_BUILD_TYPE=Release
cmake --build LASzip/build -j8

LZ=LASzip
INC="-I$LZ/dll -I$LZ/include/laszip -I$LZ/build/include/laszip"
LIB="-L$LZ/build/lib -llaszip -Wl,-rpath,$PWD/$LZ/build/lib"

cc  -O2 -o lazdump lazdump.c $INC $LIB
c++ -O2 -std=c++17 -o mklaz mklaz.cpp -I$LZ/src $INC $LIB
```

## lazdump

Decodes a LAS/LAZ file with LASzip and reports what it got.

```bash
./lazdump cloud.laz reference.txt [max_points]   # one text line per point
./lazdump cloud.laz --hash                       # whole-file FNV-1a checksum
```

## mklaz

Writes a synthetic LAS/LAZ file for a given point data format and LASzip item
version. It uses LASzip's internal API rather than the public DLL, because the
DLL always picks the default item version for a point type and the whole point
is to exercise v1 and v4 as well.

```bash
./mklaz <point_type 0-10> <version 0-4> <npoints> <chunk_size> out.laz
```

Version 0 writes uncompressed LAS. A chunk size of 0 selects LASzip's original
`POINTWISE` container -- the whole file as one stream, no chunk table -- which
only exists for point types 0-5.

The generated points deliberately sweep return numbers, scanner channels,
classification flags and GPS-time patterns so the rare branches of each coder
are reached.

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

: > ../testdata/reference_hashes.txt
for f in ../testdata/pt*; do
  echo "$(basename "$f") $(./lazdump "$f" --hash)" >> ../testdata/reference_hashes.txt
done
```
