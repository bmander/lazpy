/*
 * Derived from LASzip (https://github.com/LASzip/LASzip), lasquadtree.cpp,
 * lasinterval.cpp and lasindex.cpp.
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_indexbuild.h -- building the spatial index laz_index.h reads.
 *
 * What lasindex does: a quadtree over the area the points cover, every point
 * in the leaf it falls in as a run of consecutive point indices, and then a
 * coarsening that trades a little decoding for a much smaller index.
 */
#ifndef LAZ_INDEXBUILD_H
#define LAZ_INDEXBUILD_H

#include "laz_index.h"
#include "laz_stream.h"

/*
 * What merge_intervals writes over a run it is about to fold into the one
 * before it. No real run starts there: a start of U64_MAX would be a point
 * index no file can hold.
 */
#define LAZ_RUN_MERGED ((U64)-1)

/*
 * One cell of an index being built: which runs of point indices have landed
 * in it, and how many points that is -- which is not the same number, since a
 * run may span points that fell in other cells.
 */
typedef struct {
    LazInterval *intervals;
    U32 count, alloc;
    U64 full;
    I32 index;
} LazBuildCell;

/*
 * An index under construction. The quadtree is the same one laz_index.c
 * queries, minus the parts only a reader needs: the levels and the box are
 * what a query descends by, and what gets written out.
 */
typedef struct {
    F32 cell_size, min_x, max_x, min_y, max_y;
    U32 levels;
    U32 level_offset[17];
    LazBuildCell *cells;                /* ascending by index */
    U32 num_cells, cells_alloc;
    /* how far apart two points in a cell may be before the second starts a
     * run of its own; LASinterval's own default is 1000 */
    U32 threshold;
    /* the cell the last point fell in, since a scan line crosses one many
     * times over before it leaves; -1 for none */
    I32 last_index;
    U32 last_cell;
    char last_error[192];
    BOOL has_error;
} LazIndexBuilder;

/*
 * A tree over this area whose leaves are about `cell_size` across. `b` need
 * not be zeroed; this does that. False leaves the reason in `last_error`.
 */
BOOL laz_indexbuilder_setup(LazIndexBuilder *b, F64 min_x, F64 max_x,
                            F64 min_y, F64 max_y, F32 cell_size,
                            U32 threshold);

/* One point, at its georeferenced coordinate. Points arrive in file order. */
BOOL laz_indexbuilder_add(LazIndexBuilder *b, F64 x, F64 y, U64 point_index);

/*
 * Coarsens the tree, once every point is in it: cells holding fewer than
 * `minimum_points` between them merge into their parent, and runs merge
 * until at most `maximum_intervals` are left -- negative meaning that many
 * per cell, which is how lasindex is usually asked.
 */
BOOL laz_indexbuilder_complete(LazIndexBuilder *b, U32 minimum_points,
                               I32 maximum_intervals);

/*
 * The index written out, which is a ".lax" file's whole contents and what
 * laz_index_read reads back: the LASX header, the quadtree, then the cells
 * and their runs.
 */
BOOL laz_indexbuilder_serialize(LazIndexBuilder *b, LazOutStream *out);

void laz_indexbuilder_destroy(LazIndexBuilder *b);

#endif /* LAZ_INDEXBUILD_H */
