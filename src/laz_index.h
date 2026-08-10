/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_index.h -- the LASzip spatial index, as far as reading one goes.
 *
 * Ported from lasindex.{hpp,cpp}, lasquadtree.{hpp,cpp} and
 * lasinterval.{hpp,cpp}. A ".lax" file answers one question: given a query
 * rectangle, which runs of consecutive point indices could hold a point inside
 * it? Answering it takes two structures, both of which the file carries:
 *
 *   - a quadtree over the file's bounding box, whose leaves are the cells
 *     points were bucketed into. The tree is adaptive: a cell that held few
 *     enough points was merged into its parent when the index was built, so
 *     the leaves sit at whatever level each region needed.
 *
 *   - per cell, the intervals [start, end] of point indices that landed in it.
 *     Points arrive in file order, so a cell that a scan line crosses
 *     repeatedly gets one interval per crossing, up to the gap threshold the
 *     builder allowed.
 *
 * A query intersects the rectangle with the tree, takes the intervals of every
 * cell it hits, and merges them into one ascending list. The points in those
 * intervals are a superset of the points inside the rectangle -- a cell is
 * coarser than the query, and an interval may span points from other cells --
 * so the caller still has to test each point it decodes.
 *
 * Only reading is ported. Building an index means deciding cell sizes and
 * coarsening thresholds, which belongs with a writer; see the note in
 * tools/README.md about how the test fixtures were made.
 */
#ifndef LAZ_INDEX_H
#define LAZ_INDEX_H

#include "laz_types.h"
#include "laz_stream.h"

/* A run of consecutive point indices, both ends inclusive. */
typedef struct {
    U64 start;
    U64 end;
} LazInterval;

/*
 * The quadtree, as a ".lax" file stores it: a bounding box, a depth, and a bit
 * per cell saying whether that cell was subdivided.
 *
 * The reference class also carries a sub-level for tiling, which only its
 * tiling_setup() sets and which reading a file never does; it is left out here
 * along with the tiling and circle queries that use it.
 *
 * Coordinates are F32 rather than F64 on purpose. The file stores the bounding
 * box as four floats, and the cell midpoints the descent computes are floats
 * too, so which cell a coordinate falls into is decided in single precision.
 * Widening it here would put lazpy and laszip in different cells for a point
 * that lands on a boundary.
 */
typedef struct {
    U32 levels;
    F32 min_x, max_x, min_y, max_y;
    /* level_offset[l] is where level l's cell indices begin; the cell index of
     * the l'th-level cell with level index i is level_offset[l] + i. */
    U32 level_offset[17];
    /* One bit per cell index, set when the cell was subdivided. Absent bits
     * read as clear, so a short array simply means the rest are leaves. */
    U32 *adaptive;
    U32 adaptive_words;
    /* the cells the last intersect_rectangle hit */
    I32 *hits;
    U32 num_hits;
    U32 hits_alloc;
} LazQuadtree;

/* One cell of the index: which run of `LazIndex.intervals` is its own. The
 * file also states how many points fell in the cell, which is smaller than the
 * intervals span whenever an interval also covers a neighbour's points; no
 * query needs the difference, so it is read past rather than kept. */
typedef struct {
    I32 index;
    U32 first;
    U32 count;
} LazIndexCell;

typedef struct {
    LazQuadtree quadtree;
    LazIndexCell *cells;        /* ascending by index */
    U32 num_cells;
    LazInterval *intervals;     /* every cell's, cell-major */
    U32 num_intervals;

    /* What the last intersect_rectangle found. The buffer is kept between
     * queries rather than freed, so a reader querying repeatedly allocates
     * once. */
    LazInterval *merged;
    U32 num_merged;
    U32 merged_alloc;

    char last_error[192];
    char last_warning[192];
    BOOL has_error;
    BOOL has_warning;
} LazIndex;

/* Reads a whole ".lax" payload -- signature, quadtree and intervals -- from
 * `stream`, which is positioned at its first byte. `ix` must start zeroed;
 * laz_index_destroy leaves it that way, so one may be read into again. A read
 * that fails leaves what it managed for laz_index_destroy to free. */
BOOL laz_index_read(LazIndex *ix, LazStream *stream);

/*
 * Finds the point intervals that could hold a point inside the rectangle,
 * leaving them in `merged`/`num_merged`. A rectangle that misses the indexed
 * area entirely leaves no intervals behind and is not an error: it means the
 * query is empty, which is a perfectly good answer.
 */
BOOL laz_index_intersect_rectangle(LazIndex *ix, F64 min_x, F64 min_y,
                                   F64 max_x, F64 max_y);

void laz_index_destroy(LazIndex *ix);

#endif /* LAZ_INDEX_H */
