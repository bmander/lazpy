/*
 * Builds a LASzip spatial index for a LAS/LAZ file, using LASzip's own
 * LASquadtree and LASindex so the fixture is the reference implementation's
 * work rather than lazpy's. Writes a sidecar ".lax", or appends the index to
 * the file itself as an extended variable length record.
 *
 * The index is built from the extent of the points rather than from the
 * header's bounding box, which is what laszip's own index creation uses. The
 * files in testdata/ carry a placeholder box of a million metres each way, and
 * a quadtree over that puts all five hundred points in one cell -- an index
 * with nothing in it to test. A file whose header is right gets the same tree
 * either way.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "laszip_api.h"
#include "lasindex.hpp"
#include "lasquadtree.hpp"

static int fail(laszip_POINTER h, const char* what)
{
    laszip_CHAR* err = 0;
    if (h) laszip_get_error(h, &err);
    fprintf(stderr, "%s failed: %s\n", what, err ? err : "?");
    return 1;
}

/* Little-endian, because that is what a LAS file is; see tools/README.md for
 * why these tools are only run on a little-endian host. */
static void put_u16(unsigned char* p, unsigned v)
{
    p[0] = (unsigned char)(v & 0xFF);
    p[1] = (unsigned char)((v >> 8) & 0xFF);
}

static void put_u64(unsigned char* p, unsigned long long v)
{
    for (int i = 0; i < 8; i++) p[i] = (unsigned char)((v >> (8 * i)) & 0xFF);
}

static unsigned get_u16(const unsigned char* p) { return p[0] | (p[1] << 8); }

static unsigned get_u32(const unsigned char* p)
{
    return get_u16(p) | (get_u16(p + 2) << 16);
}

/*
 * Where the LASzip VLR's payload begins, by walking the variable length
 * records. Only needed for --append, which has to write the position of the
 * appended record into that payload.
 *
 * This is LASindex::append's own scan, reading the header out of the file
 * rather than asking the DLL for it: a reader hides the LASzip record from the
 * header it reports, so its count of the records is one short of what is
 * actually there.
 */
static long long find_laszip_vlr(FILE* file)
{
    unsigned char head[100 + 4];
    if (fseek(file, 0, SEEK_SET) != 0) return -1;
    if (fread(head, 1, sizeof(head), file) != sizeof(head)) return -1;

    long long at = (long long)get_u16(head + 94);        /* header_size */
    long long end = (long long)get_u32(head + 96);       /* to point data */

    while (at + 54 <= end) {
        unsigned char vlr[54];
        if (fseek(file, (long)at, SEEK_SET) != 0) return -1;
        if (fread(vlr, 1, sizeof(vlr), file) != sizeof(vlr)) return -1;
        /* reserved 2, user_id 16, record_id 2, record_length_after_header 2,
         * description 32 */
        if (memcmp(vlr + 2, "laszip encoded", 15) == 0) return at + 54;
        at += 54 + (long long)get_u16(vlr + 20);
    }
    return -1;
}

/*
 * Writes the index into the file as an extended record, the way LAStools'
 * lasindex -append does: the record goes at the end of the file, and the
 * LASzip VLR's "special EVLR" fields are made to point at it. Reading it back
 * means following those fields, since nothing in the LAS header mentions it.
 */
static int append_index(const LASindex& index, const char* file_name)
{
    FILE* file = fopen(file_name, "rb+");
    if (!file) { fprintf(stderr, "cannot open %s for append\n", file_name); return 1; }

    long long laz_vlr = find_laszip_vlr(file);
    if (laz_vlr < 0) {
        fprintf(stderr, "no LASzip VLR to point at the appended index\n");
        fclose(file);
        return 1;
    }

    fseek(file, 0, SEEK_END);
    long long at = ftell(file);

    unsigned char evlr[60];
    memset(evlr, 0, sizeof(evlr));
    put_u16(evlr, 0);                                   /* reserved */
    memcpy(evlr + 2, "LAStools", 8);                    /* user id */
    put_u16(evlr + 18, 30);                             /* record id */
    /* record_length_after_header is filled in once the index is written */
    snprintf((char*)evlr + 28, 32, "LAX spatial indexing (LASindex)");
    fwrite(evlr, 1, sizeof(evlr), file);

    if (!index.write(file)) {
        fprintf(stderr, "writing the appended index failed\n");
        fclose(file);
        return 1;
    }
    long long end = ftell(file);

    unsigned char length[8];
    put_u64(length, (unsigned long long)(end - at - 60));
    fseek(file, (long)(at + 20), SEEK_SET);
    fwrite(length, 1, sizeof(length), file);

    /* number_of_special_evlrs and offset_to_special_evlrs sit 16 bytes into
     * the LASzip VLR's payload, behind the compressor, coder, version,
     * options and chunk size. */
    unsigned char pointers[16];
    put_u64(pointers, 1);
    put_u64(pointers + 8, (unsigned long long)at);
    fseek(file, (long)(laz_vlr + 16), SEEK_SET);
    fwrite(pointers, 1, sizeof(pointers), file);

    fclose(file);
    return 0;
}

int main(int argc, char** argv)
{
    if (argc < 5) {
        fprintf(stderr, "usage: mklax in.laz <cell_size> <minimum_points> "
                        "<maximum_intervals> [--append]\n");
        return 1;
    }
    const char* file_name = argv[1];
    float cell_size = (float)atof(argv[2]);
    unsigned minimum_points = (unsigned)atoi(argv[3]);
    int maximum_intervals = atoi(argv[4]);
    bool append = (argc > 5 && strcmp(argv[5], "--append") == 0);

    laszip_POINTER reader;
    if (laszip_create(&reader)) return fail(0, "creating the reader");

    laszip_BOOL is_compressed = 0;
    if (laszip_open_reader(reader, file_name, &is_compressed))
        return fail(reader, "opening the reader");

    laszip_header_struct* header;
    laszip_get_header_pointer(reader, &header);
    laszip_point_struct* point;
    laszip_get_point_pointer(reader, &point);

    laszip_I64 npoints = header->number_of_point_records;
    if (npoints == 0) npoints = (laszip_I64)header->extended_number_of_point_records;
    if (npoints == 0) { fprintf(stderr, "no points to index\n"); return 1; }

    /* first pass: what the points actually span */
    double min_x = 0, max_x = 0, min_y = 0, max_y = 0;
    for (laszip_I64 i = 0; i < npoints; i++) {
        if (laszip_read_point(reader)) return fail(reader, "reading a point");
        double x = header->x_scale_factor * point->X + header->x_offset;
        double y = header->y_scale_factor * point->Y + header->y_offset;
        if (i == 0) { min_x = max_x = x; min_y = max_y = y; }
        if (x < min_x) min_x = x;
        if (x > max_x) max_x = x;
        if (y < min_y) min_y = y;
        if (y > max_y) max_y = y;
    }

    LASquadtree* quadtree = new LASquadtree;
    if (!quadtree->setup(min_x, max_x, min_y, max_y, cell_size)) {
        fprintf(stderr, "setting up the quadtree failed\n");
        return 1;
    }

    LASindex index;
    index.prepare(quadtree, 1000);

    /* second pass: which cell each point falls in */
    if (laszip_seek_point(reader, 0)) return fail(reader, "seeking to the start");
    for (laszip_I64 i = 0; i < npoints; i++) {
        if (laszip_read_point(reader)) return fail(reader, "reading a point");
        index.add(header->x_scale_factor * point->X + header->x_offset,
                  header->y_scale_factor * point->Y + header->y_offset,
                  (laszip_U64)i);
    }
    laszip_close_reader(reader);
    laszip_destroy(reader);

    index.complete(minimum_points, maximum_intervals);

    fprintf(stderr, "%lld points over %g %g %g %g, cell size %g, %u levels\n",
            (long long)npoints, min_x, min_y, max_x, max_y, cell_size,
            quadtree->levels);

    if (append) return append_index(index, file_name);

    if (!index.write(file_name)) {          /* replaces the extension with .lax */
        fprintf(stderr, "writing the index failed\n");
        return 1;
    }
    return 0;
}
