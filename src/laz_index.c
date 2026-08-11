/*
 * Derived from LASzip (https://github.com/LASzip/LASzip), lasindex.cpp,
 * lasquadtree.cpp and lasinterval.cpp.
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

#include <stdio.h>
#include <stdarg.h>
#include "laz_index.h"

/*
 * The largest gap between two intervals that still merges them into one.
 *
 * LASinterval's constructor default, which is the threshold lasindex.cpp reads
 * an index with -- the value the index was *built* with is not stored in the
 * file, so this is a property of the reader rather than of the file. Merging
 * too eagerly costs a few decoded points that the caller then rejects; merging
 * too little costs a seek. Neither changes which points a query returns.
 */
#define MERGE_THRESHOLD 1000

static void set_error(LazIndex *ix, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(ix->last_error, sizeof(ix->last_error), fmt, ap);
    va_end(ap);
    ix->has_error = LAZ_TRUE;
}

static void set_warning(LazIndex *ix, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(ix->last_warning, sizeof(ix->last_warning), fmt, ap);
    va_end(ap);
    ix->has_warning = LAZ_TRUE;
}

/*
 * Doubling growth for the arrays that are filled cell by cell.
 *
 * Neither the number of cells nor the number of intervals is known until the
 * last one has been read, and the counts the file states are not worth an
 * allocation of their own: a corrupt one would ask for gigabytes before the
 * short read that exposes it. Growing as the data actually arrives bounds the
 * damage by the size of the file. Returns NULL and leaves `base` valid on
 * failure, as realloc does.
 */
void *laz_index_grow(void *base, U32 *alloc, U32 needed, size_t item)
{
    U32 n = *alloc ? *alloc : 16;
    void *p;

    if (needed <= *alloc) return base;
    while (n < needed) {
        if (n > U32_MAX / 2) { n = needed; break; }
        n *= 2;
    }
    if ((size_t)n > (size_t)-1 / item) return NULL;
    p = realloc(base, (size_t)n * item);
    if (!p) return NULL;
    *alloc = n;
    return p;
}

/* ========================================================== the quadtree == */

/* Which level a cell index belongs to. The reference stops at 15 whatever the
 * tree's own depth, and so does this. */
U32 laz_index_level_of(const U32 *level_offset, U32 cell_index)
{
    U32 level = 0;
    while ((level < 15) && (cell_index >= level_offset[level + 1])) level++;
    return level;
}

/* Where each level's cell indices begin, which is the tree's shape and not
 * any one file's: level l has 4^l cells, all of them below level l+1's. */
void laz_index_level_offsets(U32 *level_offset)
{
    U32 l;
    level_offset[0] = 0;
    for (l = 0; l < 16; l++)
        level_offset[l + 1] = level_offset[l] + ((1u << l) * (1u << l));
}

static U32 get_cell_index(const LazQuadtree *q, U32 level_index, U32 level)
{
    return level_index + q->level_offset[level];
}

/* Makes room for `words` words of the subdivision bitmap, the new ones clear. */
static BOOL adaptive_reserve(LazQuadtree *q, U32 words)
{
    U32 was = q->adaptive_words;
    U32 *grown;

    if (words <= was) return LAZ_TRUE;
    grown = (U32 *)laz_index_grow(q->adaptive, &q->adaptive_words, words, sizeof(U32));
    if (!grown) return LAZ_FALSE;
    q->adaptive = grown;
    memset(q->adaptive + was, 0,
           (size_t)(q->adaptive_words - was) * sizeof(U32));
    return LAZ_TRUE;
}

/*
 * Records that `cell_index` is a cell the index holds points for: the cell
 * itself is a leaf, and every ancestor of it is an interior node.
 *
 * Ported from LASquadtree::manage_cell. Walking up stops at the first ancestor
 * already marked, since everything above it was marked when that one was.
 */
static BOOL quadtree_manage_cell(LazIndex *ix, U32 cell_index)
{
    LazQuadtree *q = &ix->quadtree;
    U32 max_cell_index = q->level_offset[q->levels + 1] - 1;
    U32 level, level_index, pos, bit;

    if (cell_index > max_cell_index) {
        set_error(ix, "spatial index cell %u is out of range (max %u)",
                  cell_index, max_cell_index);
        return LAZ_FALSE;
    }
    if (!adaptive_reserve(q, cell_index / 32 + 1)) {
        set_error(ix, "out of memory reading the spatial index");
        return LAZ_FALSE;
    }

    q->adaptive[cell_index / 32] &= ~(((U32)1) << (cell_index % 32));

    level = laz_index_level_of(q->level_offset, cell_index);
    level_index = cell_index - q->level_offset[level];
    while (level) {
        U32 index;
        level--;
        level_index >>= 2;
        index = get_cell_index(q, level_index, level);
        pos = index / 32;
        bit = ((U32)1) << (index % 32);
        if (pos >= q->adaptive_words) break;
        if (q->adaptive[pos] & bit) break;
        q->adaptive[pos] |= bit;
    }
    return LAZ_TRUE;
}

static BOOL hits_push(LazQuadtree *q, U32 cell_index)
{
    I32 *grown = (I32 *)laz_index_grow(q->hits, &q->hits_alloc, q->num_hits + 1,
                             sizeof(I32));
    if (!grown) return LAZ_FALSE;
    q->hits = grown;
    q->hits[q->num_hits++] = (I32)cell_index;
    return LAZ_TRUE;
}

/*
 * What a query is asking, in the coordinates the index is in.
 *
 * The rectangle is what the descent follows either way: a circle's is the
 * square around it, and `radius` above zero is what tells a leaf it has one
 * more question to answer. LASquadtree keeps the two apart, in two copies of
 * the same nine-way descent; they are one here.
 */
typedef struct {
    F64 min_x, min_y, max_x, max_y;
    F64 center_x, center_y, radius;
} LazQuery;

/*
 * Whether a circle reaches into a cell, which is what a leaf of a circle
 * query still has to be asked.
 *
 * Ported from LASquadtree::intersect_circle_with_rectangle: the nearest point
 * of the cell to the centre, by which side of the centre the cell lies on,
 * and then whether that point is inside. Strictly inside, as there.
 */
static BOOL circle_meets_cell(const LazQuery *query,
                              F32 cell_min_x, F32 cell_max_x,
                              F32 cell_min_y, F32 cell_max_y)
{
    F64 dx = 0.0, dy = 0.0;

    if (cell_max_x < query->center_x) dx = query->center_x - cell_max_x;
    else if (cell_min_x > query->center_x) dx = cell_min_x - query->center_x;
    if (cell_max_y < query->center_y) dy = query->center_y - cell_max_y;
    else if (cell_min_y > query->center_y) dy = cell_min_y - query->center_y;

    return dx * dx + dy * dy < query->radius * query->radius;
}

/*
 * Descends the tree, collecting every leaf whose square meets the query.
 *
 * Ported from LASquadtree::intersect_rectangle_with_cells_adaptive, which its
 * circle counterpart follows branch for branch: a circle descends by the
 * square around it and differs only in what a leaf has to pass, so the two
 * are one function here with the leaf test in the query.
 *
 * The midpoints are F32 and deliberately volatile, as they are in the
 * reference: they decide which side of a split a coordinate falls on, and a
 * compiler that kept them in a wider register would put a boundary case in a
 * different cell than laszip does.
 *
 * The comparisons are the reference's, asymmetric on purpose: a cell owns its
 * lower edge and not its upper one, so `r_max <= mid` means the query stays
 * below the split and `!(r_min < mid)` means it stays above.
 */
static BOOL intersect_cells(LazQuadtree *q, const LazQuery *query,
                            F32 cell_min_x, F32 cell_max_x,
                            F32 cell_min_y, F32 cell_max_y,
                            U32 level, U32 level_index)
{
    F64 r_min_x = query->min_x, r_min_y = query->min_y;
    F64 r_max_x = query->max_x, r_max_y = query->max_y;
    volatile F32 cell_mid_x;
    volatile F32 cell_mid_y;
    U32 cell_index = get_cell_index(q, level_index, level);
    U32 pos = cell_index / 32;
    U32 bit = ((U32)1) << (cell_index % 32);
    BOOL below_x, above_x, below_y, above_y;

    if (!((level < q->levels) && (pos < q->adaptive_words) &&
          (q->adaptive[pos] & bit))) {
        if (query->radius > 0 &&
            !circle_meets_cell(query, cell_min_x, cell_max_x,
                               cell_min_y, cell_max_y))
            return LAZ_TRUE;                    /* a corner the circle misses */
        return hits_push(q, cell_index);
    }

    level++;
    level_index <<= 2;
    cell_mid_x = (cell_min_x + cell_max_x) / 2;
    cell_mid_y = (cell_min_y + cell_max_y) / 2;

    below_x = (r_max_x <= cell_mid_x);
    above_x = !(r_min_x < cell_mid_x);
    below_y = (r_max_y <= cell_mid_y);
    above_y = !(r_min_y < cell_mid_y);

    /* The reference spells all nine combinations out; a child is visited in
     * exactly those where the rectangle reaches its half in both axes. Bit 0
     * of a child's level index is the upper half in x, bit 1 the upper half
     * in y. */
    if (!above_x && !above_y &&
        !intersect_cells(q, query, cell_min_x, cell_mid_x,
                         cell_min_y, cell_mid_y, level, level_index))
        return LAZ_FALSE;
    if (!below_x && !above_y &&
        !intersect_cells(q, query, cell_mid_x, cell_max_x,
                         cell_min_y, cell_mid_y, level, level_index | 1))
        return LAZ_FALSE;
    if (!above_x && !below_y &&
        !intersect_cells(q, query, cell_min_x, cell_mid_x,
                         cell_mid_y, cell_max_y, level, level_index | 2))
        return LAZ_FALSE;
    if (!below_x && !below_y &&
        !intersect_cells(q, query, cell_mid_x, cell_max_x,
                         cell_mid_y, cell_max_y, level, level_index | 3))
        return LAZ_FALSE;
    return LAZ_TRUE;
}

/* Every cell of the tree that meets the query, left in `q->hits`. */
static BOOL quadtree_intersect(LazQuadtree *q, const LazQuery *query)
{
    q->num_hits = 0;
    if (query->max_x <= q->min_x || !(query->min_x <= q->max_x) ||
        query->max_y <= q->min_y || !(query->min_y <= q->max_y))
        return LAZ_TRUE;                         /* misses the indexed area */
    return intersect_cells(q, query, q->min_x, q->max_x, q->min_y, q->max_y,
                           0, 0);
}

static BOOL quadtree_read(LazIndex *ix, LazStream *s)
{
    LazQuadtree *q = &ix->quadtree;
    U8 sig[4];
    U8 box[16];
    U32 type;

    laz_index_level_offsets(q->level_offset);

    laz_stream_get_bytes(s, sig, 4);
    if (memcmp(sig, "LASS", 4) != 0) {
        set_error(ix, "spatial index is not a LASspatial record");
        return LAZ_FALSE;
    }
    type = laz_stream_get32(s);
    if (type != 0) {
        set_error(ix, "unknown LASspatial type %u", type);
        return LAZ_FALSE;
    }

    /* Indexes written before the quadtree grew a signature of its own put the
     * level count where the signature now goes, so a mismatch is not an error
     * but the older layout. */
    laz_stream_get_bytes(s, sig, 4);
    if (memcmp(sig, "LASQ", 4) == 0) {
        (void)laz_stream_get32(s);               /* version */
        q->levels = laz_stream_get32(s);
    } else {
        q->levels = laz_le_get32(sig);
    }
    if (q->levels > 15) {
        set_error(ix, "spatial index has %u levels (max 15)", q->levels);
        return LAZ_FALSE;
    }

    /* The two fields a tiling would use. Reading an index never sets them --
     * the reference discards them here as well -- so the tree is always a
     * whole one rather than a sub-tree of a larger tiling. */
    (void)laz_stream_get32(s);                   /* level_index */
    (void)laz_stream_get32(s);                   /* implicit_levels */

    laz_stream_get_bytes(s, box, 16);
    q->min_x = laz_le_get_f32(box + 0);
    q->max_x = laz_le_get_f32(box + 4);
    q->min_y = laz_le_get_f32(box + 8);
    q->max_y = laz_le_get_f32(box + 12);

    if (laz_stream_eof(s)) {
        set_error(ix, "spatial index ends inside its quadtree");
        return LAZ_FALSE;
    }
    return LAZ_TRUE;
}

/* ========================================================== the intervals = */

static int cell_cmp(const void *a, const void *b)
{
    const LazIndexCell *x = (const LazIndexCell *)a;
    const LazIndexCell *y = (const LazIndexCell *)b;
    if (x->index != y->index) return x->index < y->index ? -1 : 1;
    /* Ties go to whichever came first in the file, so that a repeated cell
     * index keeps its first entry -- which is what the reference's hash insert
     * does with one. `first` rises with file order. */
    return x->first < y->first ? -1 : (x->first > y->first ? 1 : 0);
}

static int interval_cmp(const void *a, const void *b)
{
    const LazInterval *x = (const LazInterval *)a;
    const LazInterval *y = (const LazInterval *)b;
    if (x->start != y->start) return x->start < y->start ? -1 : 1;
    return x->end < y->end ? -1 : (x->end > y->end ? 1 : 0);
}

/* The cell with this index, or NULL. */
static const LazIndexCell *find_cell(const LazIndex *ix, I32 cell_index)
{
    U32 lo = 0, hi = ix->num_cells;
    while (lo < hi) {
        U32 mid = lo + (hi - lo) / 2;
        if (ix->cells[mid].index == cell_index) return &ix->cells[mid];
        if (ix->cells[mid].index < cell_index) lo = mid + 1;
        else hi = mid;
    }
    return NULL;
}

static BOOL interval_read(LazIndex *ix, LazStream *s)
{
    U8 sig[4];
    /* The allocated sizes are local because nothing grows these arrays again
     * once the file has been read; what survives is how much of them is used. */
    U32 version, number_cells, cells_alloc = 0, intervals_alloc = 0;
    BOOL wide;
    U32 c;

    laz_stream_get_bytes(s, sig, 4);
    if (memcmp(sig, "LASV", 4) != 0) {
        set_error(ix, "spatial index is missing its interval record");
        return LAZ_FALSE;
    }
    version = laz_stream_get32(s);
    /* Version 1 widened the point indices to 64 bits, for files with more
     * points than a U32 can number. */
    wide = (version == 1);
    number_cells = laz_stream_get32(s);

    for (c = 0; c < number_cells; c++) {
        LazIndexCell *cell;
        LazIndexCell *grown_cells;
        U32 number_intervals, i;

        if (laz_stream_eof(s)) {
            set_error(ix, "spatial index ends after %u of %u cells",
                      c, number_cells);
            return LAZ_FALSE;
        }

        grown_cells = (LazIndexCell *)laz_index_grow(ix->cells, &cells_alloc,
                                           ix->num_cells + 1,
                                           sizeof(LazIndexCell));
        if (!grown_cells) {
            set_error(ix, "out of memory reading the spatial index");
            return LAZ_FALSE;
        }
        ix->cells = grown_cells;
        cell = &ix->cells[ix->num_cells++];

        cell->index = (I32)laz_stream_get32(s);
        number_intervals = laz_stream_get32(s);
        /* how many points fell in the cell, as against how many the intervals
         * span; read past because nothing here has a use for it */
        if (wide) (void)laz_stream_get64(s); else (void)laz_stream_get32(s);
        cell->first = ix->num_intervals;
        cell->count = 0;

        for (i = 0; i < number_intervals; i++) {
            LazInterval *iv;
            LazInterval *grown = (LazInterval *)laz_index_grow(ix->intervals,
                                                     &intervals_alloc,
                                                     ix->num_intervals + 1,
                                                     sizeof(LazInterval));
            if (!grown) {
                set_error(ix, "out of memory reading the spatial index");
                return LAZ_FALSE;
            }
            ix->intervals = grown;
            iv = &ix->intervals[ix->num_intervals];

            if (wide) {
                iv->start = laz_stream_get64(s);
                iv->end = laz_stream_get64(s);
            } else {
                iv->start = laz_stream_get32(s);
                iv->end = laz_stream_get32(s);
            }
            if (laz_stream_eof(s)) {
                set_error(ix, "spatial index ends inside cell %d", cell->index);
                return LAZ_FALSE;
            }
            if (iv->end < iv->start) {
                set_error(ix, "spatial index interval ends at %llu before it "
                          "starts at %llu", (unsigned long long)iv->end,
                          (unsigned long long)iv->start);
                return LAZ_FALSE;
            }
            cell->count++;
            ix->num_intervals++;
        }
    }
    return LAZ_TRUE;
}

/*
 * Puts the cells in index order and drops any index that appears twice.
 *
 * A repeated index is a malformed file rather than something to refuse to open
 * over -- the reference warns and ignores the later entry, and so does this.
 * The dropped cell's intervals stay in the pool, unreferenced; compacting them
 * would cost a pass over a healthy file to tidy a broken one.
 */
static void sort_cells(LazIndex *ix)
{
    U32 kept = 0, i;

    if (ix->num_cells < 2) return;
    qsort(ix->cells, ix->num_cells, sizeof(LazIndexCell), cell_cmp);
    for (i = 1; i < ix->num_cells; i++) {
        if (ix->cells[i].index == ix->cells[kept].index) continue;
        ix->cells[++kept] = ix->cells[i];
    }
    kept++;
    if (kept != ix->num_cells) {
        set_warning(ix, "spatial index states %u cells more than once; each "
                    "one keeps its first entry", ix->num_cells - kept);
        ix->num_cells = kept;
    }
}

/* ============================================================== the index = */

BOOL laz_index_read(LazIndex *ix, LazStream *stream)
{
    U8 sig[4];
    U32 i;

    laz_stream_get_bytes(stream, sig, 4);
    if (memcmp(sig, "LASX", 4) != 0) {
        set_error(ix, "not a spatial index: signature is not 'LASX'");
        return LAZ_FALSE;
    }
    (void)laz_stream_get32(stream);              /* version */

    if (!quadtree_read(ix, stream)) return LAZ_FALSE;
    if (!interval_read(ix, stream)) return LAZ_FALSE;
    sort_cells(ix);

    /* Which cells exist is what makes the tree adaptive: a cell the intervals
     * name is a leaf, and its ancestors are interior nodes. */
    for (i = 0; i < ix->num_cells; i++) {
        if (ix->cells[i].index < 0) {
            set_error(ix, "spatial index names a negative cell %d",
                      ix->cells[i].index);
            return LAZ_FALSE;
        }
        if (!quadtree_manage_cell(ix, (U32)ix->cells[i].index))
            return LAZ_FALSE;
    }
    return LAZ_TRUE;
}

static BOOL merged_reserve(LazIndex *ix, U32 needed)
{
    LazInterval *grown;
    /* Asking for nothing is not a failure, though grow() cannot say so: it
     * hands back the base pointer, which is NULL until the first allocation. */
    if (needed == 0) return LAZ_TRUE;
    grown = (LazInterval *)laz_index_grow(ix->merged, &ix->merged_alloc,
                                needed, sizeof(LazInterval));
    if (!grown) {
        set_error(ix, "out of memory answering a spatial index query");
        return LAZ_FALSE;
    }
    ix->merged = grown;
    return LAZ_TRUE;
}

/*
 * Folds the intervals of every cell the query hit into one ascending list.
 *
 * Ported from LASinterval::merge. One cell needs no work: its intervals are
 * already in order and already far enough apart. Several do, because each
 * cell's list covers the whole file and they interleave -- and two intervals
 * closer together than the threshold are worth reading through rather than
 * seeking between.
 */
static BOOL merge_hits(LazIndex *ix)
{
    const LazQuadtree *q = &ix->quadtree;
    U32 used = 0, i;

    ix->num_merged = 0;

    for (i = 0; i < q->num_hits; i++) {
        const LazIndexCell *cell = find_cell(ix, q->hits[i]);
        if (!cell) continue;                 /* a cell the query reached but
                                              * that holds no points */
        used++;
        if (!merged_reserve(ix, ix->num_merged + cell->count))
            return LAZ_FALSE;
        memcpy(ix->merged + ix->num_merged, ix->intervals + cell->first,
               (size_t)cell->count * sizeof(LazInterval));
        ix->num_merged += cell->count;
    }
    /* One cell is already in order and already far enough apart, and the
     * reference leaves it alone; running it through the threshold below would
     * join intervals it kept separate. */
    if (used < 2 || ix->num_merged < 2) return LAZ_TRUE;

    ix->num_merged = laz_index_coalesce(ix->merged, ix->num_merged,
                                        MERGE_THRESHOLD);
    return LAZ_TRUE;
}

/*
 * Puts `n` runs in order and joins the ones no more than `threshold` apart,
 * leaving how many are left.
 *
 * The rule LASinterval applies in both directions: a gap that small costs a
 * reader the few points inside it and saves it a seek, so the two runs are
 * one. Shared with the builder, which has to agree with this exactly.
 */
U32 laz_index_coalesce(LazInterval *intervals, U32 n, U64 threshold)
{
    U32 i, kept = 0;

    if (n < 2) return n;
    qsort(intervals, n, sizeof(LazInterval), interval_cmp);
    for (i = 1; i < n; i++) {
        LazInterval next = intervals[i];
        if ((I64)next.start - (I64)intervals[kept].end > (I64)threshold)
            intervals[++kept] = next;
        else if (next.end > intervals[kept].end)
            intervals[kept].end = next.end;
    }
    return kept + 1;
}

static BOOL intersect(LazIndex *ix, const LazQuery *query)
{
    ix->num_merged = 0;
    /* An inverted rectangle holds nothing. Said here because the descent's
     * comparisons assume otherwise -- a rectangle can be below a split and
     * above it at once only if it is empty. */
    if (query->min_x > query->max_x || query->min_y > query->max_y)
        return LAZ_TRUE;
    if (!quadtree_intersect(&ix->quadtree, query)) {
        set_error(ix, "out of memory answering a spatial index query");
        return LAZ_FALSE;
    }
    if (!ix->quadtree.num_hits) return LAZ_TRUE;
    return merge_hits(ix);
}

BOOL laz_index_intersect_rectangle(LazIndex *ix, F64 min_x, F64 min_y,
                                   F64 max_x, F64 max_y)
{
    LazQuery query;
    query.min_x = min_x;
    query.min_y = min_y;
    query.max_x = max_x;
    query.max_y = max_y;
    query.center_x = query.center_y = query.radius = 0.0;
    return intersect(ix, &query);
}

BOOL laz_index_intersect_circle(LazIndex *ix, F64 center_x, F64 center_y,
                                F64 radius)
{
    LazQuery query;

    if (!(radius > 0)) {                    /* a circle of no size, or NaN */
        ix->num_merged = 0;
        return LAZ_TRUE;
    }
    /* the square around the circle is what the descent follows; the circle
     * itself is what a leaf is then asked about */
    query.min_x = center_x - radius;
    query.min_y = center_y - radius;
    query.max_x = center_x + radius;
    query.max_y = center_y + radius;
    query.center_x = center_x;
    query.center_y = center_y;
    query.radius = radius;
    return intersect(ix, &query);
}

void laz_index_destroy(LazIndex *ix)
{
    free(ix->quadtree.adaptive);
    free(ix->quadtree.hits);
    free(ix->cells);
    free(ix->intervals);
    free(ix->merged);
    memset(ix, 0, sizeof(*ix));
}
