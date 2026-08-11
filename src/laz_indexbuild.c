/*
 * Derived from LASzip (https://github.com/LASzip/LASzip), lasquadtree.cpp,
 * lasinterval.cpp and lasindex.cpp.
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_indexbuild.c -- building a LASzip spatial index, the inverse of what
 * laz_index.c reads.
 *
 * Three steps, which are LASquadtree::setup, LASinterval::add and
 * LASindex::complete:
 *
 *   - a quadtree over the area the points cover, deep enough that its leaves
 *     are about `cell_size` across;
 *   - every point put in the leaf it falls in, as a run of consecutive point
 *     indices: a point that carries on where the last one in its cell left
 *     off extends that run, and one that arrives more than `threshold`
 *     points later starts another;
 *   - and then a coarsening, which is what keeps the index small: cells with
 *     few points between them are merged into their parent, and the runs with
 *     the smallest gaps between them are joined until few enough are left.
 *
 * The reference keeps its cells in a hash and its runs in linked lists. Here
 * the cells are an array kept ascending by index -- which is the order they
 * are written in, and the order a reader wants them -- and each cell's runs
 * are an array of its own. What comes out is the same index; what a reader
 * cannot tell apart is the order the cells appear in the file, which the
 * reference leaves to its hash and this leaves sorted.
 */

#include <stdio.h>
#include <stdarg.h>
#include "laz_indexbuild.h"

/* U32_QUANTIZE from LASzip's mydefs.hpp: a half rounds up, and nothing
 * below zero survives. */
#define U32_QUANTIZE(n) (((n) >= 0) ? (U32)((n) + 0.5) : (U32)0)

static void build_error(LazIndexBuilder *b, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b->last_error, sizeof(b->last_error), fmt, ap);
    va_end(ap);
    b->has_error = LAZ_TRUE;
}

/* ========================================================== the quadtree == */

/*
 * The tree a cell size implies over this area, as LASquadtree::setup.
 *
 * The box is grown to whole cells, then to the square the tree needs: a
 * quadtree of `levels` levels is 2^levels cells across, and the difference is
 * split between the two sides -- the larger half below, which is the
 * reference's rounding and decides which cell a point on a boundary lands in.
 */
BOOL laz_indexbuilder_setup(LazIndexBuilder *b, F64 min_x, F64 max_x,
                            F64 min_y, F64 max_y, F32 cell_size,
                            U32 threshold)
{
    U32 c, c1, c2, cells_x, cells_y;

    memset(b, 0, sizeof(*b));
    b->threshold = threshold;
    b->last_index = -1;

    if (!(cell_size > 0)) {
        build_error(b, "cell size %g must be positive", (double)cell_size);
        return LAZ_FALSE;
    }
    if (!(min_x <= max_x) || !(min_y <= max_y)) {
        build_error(b, "the area to index is inside out or not a number");
        return LAZ_FALSE;
    }
    b->cell_size = cell_size;

    /* out to whole cells, away from zero on the side each end is on */
    b->min_x = min_x >= 0 ? cell_size * (F32)(I32)(min_x / cell_size)
                          : cell_size * ((F32)(I32)(min_x / cell_size) - 1);
    b->max_x = max_x >= 0 ? cell_size * ((F32)(I32)(max_x / cell_size) + 1)
                          : cell_size * (F32)(I32)(max_x / cell_size);
    b->min_y = min_y >= 0 ? cell_size * (F32)(I32)(min_y / cell_size)
                          : cell_size * ((F32)(I32)(min_y / cell_size) - 1);
    b->max_y = max_y >= 0 ? cell_size * ((F32)(I32)(max_y / cell_size) + 1)
                          : cell_size * (F32)(I32)(max_y / cell_size);

    cells_x = U32_QUANTIZE((b->max_x - b->min_x) / cell_size);
    cells_y = U32_QUANTIZE((b->max_y - b->min_y) / cell_size);
    if (cells_x == 0 || cells_y == 0) {
        build_error(b, "an area of %u by %u cells has nothing to index",
                    cells_x, cells_y);
        return LAZ_FALSE;
    }

    /* the levels it takes to have that many cells across the wider side */
    c = (cells_x > cells_y ? cells_x : cells_y) - 1;
    b->levels = 0;
    while (c) { c >>= 1; b->levels++; }
    if (b->levels > 15) {
        build_error(b, "a cell size of %g needs %u levels, over the 15 an "
                    "index can hold", (double)cell_size, b->levels);
        return LAZ_FALSE;
    }

    /* and out again to the square that many levels really covers */
    c = (1u << b->levels) - cells_x;
    c1 = c / 2;
    c2 = c - c1;
    b->min_x -= c2 * cell_size;
    b->max_x += c1 * cell_size;
    c = (1u << b->levels) - cells_y;
    c1 = c / 2;
    c2 = c - c1;
    b->min_y -= c2 * cell_size;
    b->max_y += c1 * cell_size;

    laz_index_level_offsets(b->level_offset);
    return LAZ_TRUE;
}

/*
 * Which leaf a coordinate falls in, as LASquadtree::get_level_index.
 *
 * The midpoints are F32 and volatile for the reason they are in the query
 * descent: they decide which side of a split a coordinate is on, and a
 * compiler that kept them wider would put a boundary point in a different
 * cell than laszip does.
 */
static U32 build_cell_index(const LazIndexBuilder *b, F64 x, F64 y)
{
    volatile F32 cell_mid_x, cell_mid_y;
    F32 cell_min_x = b->min_x, cell_max_x = b->max_x;
    F32 cell_min_y = b->min_y, cell_max_y = b->max_y;
    U32 level_index = 0, level = b->levels;

    while (level) {
        level_index <<= 2;
        cell_mid_x = (cell_min_x + cell_max_x) / 2;
        cell_mid_y = (cell_min_y + cell_max_y) / 2;
        if (x < cell_mid_x) {
            cell_max_x = cell_mid_x;
        } else {
            cell_min_x = cell_mid_x;
            level_index |= 1;
        }
        if (y < cell_mid_y) {
            cell_max_y = cell_mid_y;
        } else {
            cell_min_y = cell_mid_y;
            level_index |= 2;
        }
        level--;
    }
    return b->level_offset[b->levels] + level_index;
}

/*
 * The parent of a cell and the four children of that parent, as
 * LASquadtree::coarsen. False for a cell at the root, which has no parent.
 */
static BOOL build_coarsen(const LazIndexBuilder *b, I32 cell_index,
                          I32 *parent, I32 siblings[4])
{
    U32 level = laz_index_level_of(b->level_offset, (U32)cell_index);
    U32 level_index, first;
    int i;

    if (cell_index < 0 || level == 0) return LAZ_FALSE;
    level_index = (U32)cell_index - b->level_offset[level];
    *parent = (I32)(b->level_offset[level - 1] + (level_index >> 2));
    first = b->level_offset[level] + ((level_index >> 2) << 2);
    for (i = 0; i < 4; i++) siblings[i] = (I32)(first + (U32)i);
    return LAZ_TRUE;
}

/* ============================================================== the cells = */

/* Where a cell index sits in the array, or where it would be inserted. */
static U32 cell_position(const LazIndexBuilder *b, I32 index, BOOL *found)
{
    U32 low = 0, high = b->num_cells;

    while (low < high) {
        U32 mid = low + (high - low) / 2;
        if (b->cells[mid].index < index) low = mid + 1;
        else high = mid;
    }
    *found = (low < b->num_cells && b->cells[low].index == index);
    return low;
}

/* Makes an empty cell at `at`, keeping the array ascending by index. */
static LazBuildCell *cell_insert(LazIndexBuilder *b, U32 at, I32 index)
{
    LazBuildCell *grown = (LazBuildCell *)laz_index_grow(
        b->cells, &b->cells_alloc, b->num_cells + 1, sizeof(LazBuildCell));
    if (!grown) {
        build_error(b, "out of memory building the spatial index");
        return NULL;
    }
    b->cells = grown;
    memmove(b->cells + at + 1, b->cells + at,
            (size_t)(b->num_cells - at) * sizeof(LazBuildCell));
    b->num_cells++;
    memset(&b->cells[at], 0, sizeof(LazBuildCell));
    b->cells[at].index = index;
    return &b->cells[at];
}

static BOOL cell_add_interval(LazIndexBuilder *b, LazBuildCell *cell,
                              U64 start, U64 end)
{
    LazInterval *grown = (LazInterval *)laz_index_grow(
        cell->intervals, &cell->alloc, cell->count + 1, sizeof(LazInterval));
    if (!grown) {
        build_error(b, "out of memory building the spatial index");
        return LAZ_FALSE;
    }
    cell->intervals = grown;
    cell->intervals[cell->count].start = start;
    cell->intervals[cell->count].end = end;
    cell->count++;
    return LAZ_TRUE;
}

/*
 * Puts one point in the cell it falls in, as LASinterval::add.
 *
 * Points arrive in file order, so a point either carries on the run its cell
 * was last extended to or, having arrived more than `threshold` points later,
 * starts another. The cell a point falls in is remembered because a scan line
 * crosses a cell many times over before it leaves.
 */
BOOL laz_indexbuilder_add(LazIndexBuilder *b, F64 x, F64 y, U64 point_index)
{
    I32 index = (I32)build_cell_index(b, x, y);
    LazBuildCell *cell;
    LazInterval *last;

    if (b->last_index == index) {
        cell = &b->cells[b->last_cell];
    } else {
        BOOL found;
        U32 at = cell_position(b, index, &found);
        if (!found) {
            cell = cell_insert(b, at, index);
            if (!cell) return LAZ_FALSE;
            cell->full = 1;
            b->last_index = index;
            b->last_cell = at;
            return cell_add_interval(b, cell, point_index, point_index);
        }
        cell = &b->cells[at];
        b->last_index = index;
        b->last_cell = at;
    }

    cell->full++;
    last = &cell->intervals[cell->count - 1];
    if (point_index - last->end > b->threshold)
        return cell_add_interval(b, cell, point_index, point_index);
    last->end = point_index;
    return LAZ_TRUE;
}

/* ============================================================ coarsening == */

/*
 * Joins the runs of several cells into one, as LASinterval::merge: in order
 * of where they start, and joined where the gap between two is no more than
 * the threshold -- the same rule that decides whether a point extends a run
 * or starts one.
 */
static BOOL cells_merge(LazIndexBuilder *b, LazBuildCell *into,
                        LazBuildCell **from, U32 num_from)
{
    U32 i;
    U64 full = 0;
    LazInterval *all = NULL;
    U32 total = 0, alloc = 0;

    for (i = 0; i < num_from; i++) {
        LazInterval *grown = (LazInterval *)laz_index_grow(
            all, &alloc, total + from[i]->count, sizeof(LazInterval));
        if (!grown) {
            free(all);
            build_error(b, "out of memory building the spatial index");
            return LAZ_FALSE;
        }
        all = grown;
        memcpy(all + total, from[i]->intervals,
               (size_t)from[i]->count * sizeof(LazInterval));
        total += from[i]->count;
        full += from[i]->full;
    }
    free(into->intervals);
    into->intervals = all;
    into->count = laz_index_coalesce(all, total, b->threshold);
    into->alloc = alloc;
    into->full = full;
    return LAZ_TRUE;
}

static void cell_free(LazBuildCell *cell)
{
    free(cell->intervals);
    cell->intervals = NULL;
    cell->count = cell->alloc = 0;
}

/* Drops the cells at the marked positions, keeping the rest in order. */
static void cells_drop(LazIndexBuilder *b, const BOOL *dropped)
{
    U32 i, kept = 0;
    for (i = 0; i < b->num_cells; i++) {
        if (dropped[i]) cell_free(&b->cells[i]);
        else b->cells[kept++] = b->cells[i];
    }
    b->num_cells = kept;
    b->last_index = -1;                 /* the cached cell may have moved */
}

static int build_cell_cmp(const void *a, const void *b)
{
    const LazBuildCell *x = (const LazBuildCell *)a;
    const LazBuildCell *y = (const LazBuildCell *)b;
    return x->index < y->index ? -1 : (x->index > y->index ? 1 : 0);
}

/*
 * Merges every group of four sibling cells that holds few enough points
 * between them, over and over until a pass changes nothing.
 *
 * LASindex::complete's rule: all four have to be there -- a parent standing
 * for a partly-filled quadrant would claim points it does not hold -- and
 * their points together have to be under `minimum_points`.
 */
static BOOL coarsen_cells(LazIndexBuilder *b, U32 minimum_points)
{
    BOOL *dropped = NULL;
    BOOL coarsened = LAZ_TRUE;

    while (coarsened) {
        U32 i;
        coarsened = LAZ_FALSE;
        dropped = (BOOL *)calloc(b->num_cells ? b->num_cells : 1,
                                 sizeof(BOOL));
        if (!dropped) {
            build_error(b, "out of memory building the spatial index");
            return LAZ_FALSE;
        }

        for (i = 0; i < b->num_cells; i++) {
            I32 parent, siblings[4];
            LazBuildCell *group[4];
            U32 positions[4], found = 0, k;
            U64 full = 0;

            if (dropped[i]) continue;
            if (!build_coarsen(b, b->cells[i].index, &parent, siblings))
                continue;

            for (k = 0; k < 4; k++) {
                BOOL is_there;
                U32 at = cell_position(b, siblings[k], &is_there);
                if (!is_there || dropped[at]) break;
                positions[found] = at;
                group[found] = &b->cells[at];
                full += b->cells[at].full;
                found++;
            }
            if (found != 4 || full >= minimum_points) continue;

            /* the parent takes their place, at the position the first of
             * them held -- the array stays ascending, since a parent's index
             * is below every one of its children's */
            if (!cells_merge(b, &b->cells[positions[0]], group, 4)) {
                free(dropped);
                return LAZ_FALSE;
            }
            b->cells[positions[0]].index = parent;
            for (k = 1; k < 4; k++) dropped[positions[k]] = LAZ_TRUE;
            coarsened = LAZ_TRUE;
        }

        if (coarsened) {
            cells_drop(b, dropped);
            /* a parent's index is below its children's, so putting it where
             * the first of them was leaves the array unsorted */
            qsort(b->cells, b->num_cells, sizeof(LazBuildCell),
                  build_cell_cmp);
        }
        free(dropped);
        dropped = NULL;
    }
    return LAZ_TRUE;
}

/* One gap between two runs of a cell, for merge_intervals to order by. */
typedef struct {
    U64 gap;
    U32 cell;
    U32 at;             /* the run this gap is in front of */
} Gap;

static int gap_cmp(const void *a, const void *b)
{
    const Gap *x = (const Gap *)a;
    const Gap *y = (const Gap *)b;
    if (x->gap != y->gap) return x->gap < y->gap ? -1 : 1;
    if (x->cell != y->cell) return x->cell < y->cell ? -1 : 1;
    return x->at < y->at ? -1 : (x->at > y->at ? 1 : 0);
}

/*
 * Joins the runs with the smallest gaps between them until no more than
 * `maximum` are left, as LASinterval::merge_intervals.
 *
 * Every cell keeps at least one run, so what is on offer is the gaps inside
 * cells, taken smallest first: joining across a small gap costs a reader the
 * few points in the gap and saves it a seek.
 *
 * The reference takes them from an ordered map one at a time, reinserting the
 * gap it makes. It need not: joining two runs leaves the gaps on either side
 * of them exactly as they were, so no gap ever changes and the smallest few
 * can be taken all at once.
 */
static BOOL merge_intervals(LazIndexBuilder *b, U32 maximum)
{
    Gap *gaps = NULL;
    U32 alloc = 0, num_gaps = 0, total = 0, i, j;

    for (i = 0; i < b->num_cells; i++) total += b->cells[i].count;
    if (maximum < b->num_cells) maximum = b->num_cells;
    if (total <= maximum) return LAZ_TRUE;

    for (i = 0; i < b->num_cells; i++) {
        const LazBuildCell *cell = &b->cells[i];
        for (j = 1; j < cell->count; j++) {
            Gap *grown = (Gap *)laz_index_grow(gaps, &alloc, num_gaps + 1,
                                               sizeof(Gap));
            if (!grown) {
                free(gaps);
                build_error(b, "out of memory building the spatial index");
                return LAZ_FALSE;
            }
            gaps = grown;
            gaps[num_gaps].gap = (cell->intervals[j].start
                                  - cell->intervals[j - 1].end);
            gaps[num_gaps].cell = i;
            gaps[num_gaps].at = j;
            num_gaps++;
        }
    }
    qsort(gaps, num_gaps, sizeof(Gap), gap_cmp);

    /* mark the runs to swallow, smallest gap first */
    for (i = 0; i < num_gaps && total > maximum; i++, total--)
        b->cells[gaps[i].cell].intervals[gaps[i].at].start = LAZ_RUN_MERGED;
    free(gaps);

    /* then close each cell up, a marked run joining the one before it */
    for (i = 0; i < b->num_cells; i++) {
        LazBuildCell *cell = &b->cells[i];
        U32 kept = 0;
        for (j = 1; j < cell->count; j++) {
            if (cell->intervals[j].start == LAZ_RUN_MERGED)
                cell->intervals[kept].end = cell->intervals[j].end;
            else
                cell->intervals[++kept] = cell->intervals[j];
        }
        cell->count = cell->count ? kept + 1 : 0;
    }
    return LAZ_TRUE;
}

BOOL laz_indexbuilder_complete(LazIndexBuilder *b, U32 minimum_points,
                               I32 maximum_intervals)
{
    U32 maximum;

    if (minimum_points && !coarsen_cells(b, minimum_points)) return LAZ_FALSE;

    /* a negative count is that many per cell, which is how the reference
     * spells "keep the index proportionate to the tree" */
    if (maximum_intervals < 0)
        maximum = (U32)(-(I64)maximum_intervals) * b->num_cells;
    else
        maximum = (U32)maximum_intervals;
    if (maximum) return merge_intervals(b, maximum);
    return LAZ_TRUE;
}

/* ========================================================= writing it out = */

/* The one thing the stream has no putter for. */
static void put_f32(LazOutStream *out, F32 value)
{
    U8 bytes[4];
    laz_le_put_f32(bytes, value);
    laz_outstream_put_bytes(out, bytes, 4);
}

static void put_signature(LazOutStream *out, const char *four)
{
    laz_outstream_put_bytes(out, (const U8 *)four, 4);
}

/* Whether any point index needs more than 32 bits, which is what the
 * interval record's version 1 is for. */
static BOOL needs_wide_intervals(const LazIndexBuilder *b)
{
    U32 i, j;
    for (i = 0; i < b->num_cells; i++)
        for (j = 0; j < b->cells[i].count; j++)
            if (b->cells[i].intervals[j].end > 0xFFFFFFFFu) return LAZ_TRUE;
    return LAZ_FALSE;
}

BOOL laz_indexbuilder_serialize(LazIndexBuilder *b, LazOutStream *out)
{
    BOOL wide = needs_wide_intervals(b);
    U32 i, j;

    put_signature(out, "LASX");
    laz_outstream_put32(out, 0);                /* version */

    put_signature(out, "LASS");
    laz_outstream_put32(out, 0);                /* a quadtree, not a grid */
    put_signature(out, "LASQ");
    laz_outstream_put32(out, 0);                /* version */
    laz_outstream_put32(out, b->levels);
    laz_outstream_put32(out, 0);                /* level index: no tiling */
    laz_outstream_put32(out, 0);                /* implicit levels */
    put_f32(out, b->min_x);
    put_f32(out, b->max_x);
    put_f32(out, b->min_y);
    put_f32(out, b->max_y);

    put_signature(out, "LASV");
    /* version 1 widens the point indices, for a file with more points than a
     * U32 can number */
    laz_outstream_put32(out, wide ? 1 : 0);
    laz_outstream_put32(out, b->num_cells);
    for (i = 0; i < b->num_cells; i++) {
        const LazBuildCell *cell = &b->cells[i];
        laz_outstream_put32(out, (U32)cell->index);
        laz_outstream_put32(out, cell->count);
        if (wide) laz_outstream_put64(out, cell->full);
        else laz_outstream_put32(out, (U32)cell->full);
        for (j = 0; j < cell->count; j++) {
            if (wide) {
                laz_outstream_put64(out, cell->intervals[j].start);
                laz_outstream_put64(out, cell->intervals[j].end);
            } else {
                laz_outstream_put32(out, (U32)cell->intervals[j].start);
                laz_outstream_put32(out, (U32)cell->intervals[j].end);
            }
        }
    }

    if (out->failed) {
        build_error(b, "could not write the spatial index out");
        return LAZ_FALSE;
    }
    return LAZ_TRUE;
}

void laz_indexbuilder_destroy(LazIndexBuilder *b)
{
    U32 i;
    for (i = 0; i < b->num_cells; i++) cell_free(&b->cells[i]);
    free(b->cells);
    memset(b, 0, sizeof(*b));
}
