/* Point data record sizes for LAS formats 0-10, before any extra bytes.
 *
 * Shared by lazdump and mklaz, which both have to work out where a record's
 * extra bytes begin. It is the same table as lazpy._POINT_FORMATS, which these
 * tools cannot reach: they are standalone programs built against liblaszip,
 * not against lazpy. Keeping one copy for the two of them at least means the
 * fixtures and the hashes taken over them cannot disagree.
 *
 * Compiles as C and as C++.
 */
#ifndef LAZPY_TOOLS_POINT_SIZES_H
#define LAZPY_TOOLS_POINT_SIZES_H

static const unsigned short point_base_size[11] =
    { 20, 28, 26, 34, 57, 63, 30, 36, 38, 59, 67 };

#endif /* LAZPY_TOOLS_POINT_SIZES_H */
