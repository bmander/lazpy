/* Reference point dumper: decodes a LAS/LAZ file with laszip and writes one
 * line per point with every decoded field. Used as ground truth for lazpy. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "laszip_api.h"
#include "point_sizes.h"

/*
 * FNV-1a over a canonical per-point byte record. Both this tool and
 * tools/compare_with_laszip.py build the same record, so a single 64-bit value
 * verifies every field of every point in a file too large to dump as text.
 */
static unsigned long long fnv1a(unsigned long long h, const unsigned char* p, int n)
{
    for (int i = 0; i < n; i++) {
        h ^= p[i];
        h *= 1099511628211ULL;
    }
    return h;
}

/* Little-endian, not host order: the hash of a file must be the same number
 * wherever it is computed, so that testdata/reference_hashes.txt can be
 * compared against a lazpy built for any host. */
static void put_u16(unsigned char* p, unsigned v)
{
    p[0] = (unsigned char)(v & 0xFF);
    p[1] = (unsigned char)((v >> 8) & 0xFF);
}

static void put_u32(unsigned char* p, unsigned v)
{
    put_u16(p, v & 0xFFFF);
    put_u16(p + 2, (v >> 16) & 0xFFFF);
}

static void put_i32(unsigned char* p, int v) { put_u32(p, (unsigned)v); }

static void put_u64(unsigned char* p, unsigned long long v)
{
    put_u32(p, (unsigned)(v & 0xFFFFFFFFu));
    put_u32(p + 4, (unsigned)(v >> 32));
}

int main(int argc, char** argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: lazdump in.laz out.txt [max_points]\n");
        fprintf(stderr, "       lazdump in.laz --hash\n");
        return 1;
    }
    int hash_mode = (strcmp(argv[2], "--hash") == 0);
    long long max_points = (argc > 3) ? atoll(argv[3]) : -1;

    laszip_POINTER reader;
    if (laszip_create(&reader)) { fprintf(stderr, "create failed\n"); return 1; }

    /* Ask for LAS 1.4 compatibility mode, which lazpy always applies: a legacy
     * file that turns out to be a disguised LAS 1.4 one is then reported as
     * the 1.4 file it is, with its points put back together. Files that are
     * not disguised are unaffected. */
    if (laszip_request_compatibility_mode(reader, 1)) {
        fprintf(stderr, "requesting compatibility mode failed\n");
        return 1;
    }

    laszip_BOOL is_compressed = 0;
    if (laszip_open_reader(reader, argv[1], &is_compressed)) {
        laszip_CHAR* err; laszip_get_error(reader, &err);
        fprintf(stderr, "open failed: %s\n", err ? err : "?");
        return 1;
    }

    laszip_header_struct* header;
    laszip_get_header_pointer(reader, &header);
    laszip_point_struct* point;
    laszip_get_point_pointer(reader, &point);

    /* LAS 1.4 zeroes the legacy count for the extended point formats */
    laszip_I64 npoints = header->number_of_point_records;
    if (npoints == 0) npoints = (laszip_I64)header->extended_number_of_point_records;

    /*
     * How many extra bytes a point really has.
     *
     * Normally that is point->num_extra_bytes, but a compatibility-mode file
     * leaves it as the raw item size: the five (or seven) bytes the LAS 1.4
     * fields travelled in are still in the buffer even though the upgraded
     * header no longer counts them, and laszip never trims it. Believing the
     * header is what reports the extra bytes the file is actually about, and
     * is what lazpy hands back.
     */
    int num_extra_bytes = point->num_extra_bytes;
    if (header->point_data_format <= 10) {
        num_extra_bytes = (int)header->point_data_record_length
                          - point_base_size[header->point_data_format];
        if (num_extra_bytes < 0) num_extra_bytes = 0;
    }

    FILE* out = NULL;
    if (!hash_mode) {
        out = fopen(argv[2], "w");
        if (!out) { fprintf(stderr, "cannot write %s\n", argv[2]); return 1; }
    }

    if (!hash_mode) {
        fprintf(out, "# format=%d npoints=%lld compressed=%d version=1.%d\n",
                header->point_data_format, (long long)npoints, is_compressed,
                header->version_minor);
    }

    laszip_I64 limit = npoints;
    if (max_points >= 0 && max_points < limit) limit = max_points;

    unsigned long long hash = 14695981039346656037ULL;

    for (laszip_I64 i = 0; i < limit; i++) {
        if (laszip_read_point(reader)) {
            laszip_CHAR* err; laszip_get_error(reader, &err);
            fprintf(stderr, "read failed at %lld: %s\n", (long long)i, err ? err : "?");
            return 1;
        }
        /* gps_time is printed as raw IEEE754 bits so comparison is exact */
        unsigned long long gps_bits;
        memcpy(&gps_bits, &point->gps_time, 8);

        if (hash_mode) {
            /* canonical 64-byte record; must match compare_with_laszip.py */
            unsigned char rec[64];
            memset(rec, 0, sizeof(rec));
            put_i32(rec + 0, point->X);
            put_i32(rec + 4, point->Y);
            put_i32(rec + 8, point->Z);
            put_u16(rec + 12, point->intensity);
            rec[14] = (unsigned char)(point->return_number
                       | (point->number_of_returns << 3)
                       | (point->scan_direction_flag << 6)
                       | (point->edge_of_flight_line << 7));
            rec[15] = (unsigned char)(point->classification
                       | (point->synthetic_flag << 5)
                       | (point->keypoint_flag << 6)
                       | (point->withheld_flag << 7));
            rec[16] = (unsigned char)point->scan_angle_rank;
            rec[17] = point->user_data;
            put_u16(rec + 18, point->point_source_ID);
            put_u64(rec + 20, gps_bits);
            put_u16(rec + 28, point->rgb[0]);
            put_u16(rec + 30, point->rgb[1]);
            put_u16(rec + 32, point->rgb[2]);
            put_u16(rec + 34, point->rgb[3]);
            put_u16(rec + 36, (unsigned)(unsigned short)point->extended_scan_angle);
            rec[38] = (unsigned char)(point->extended_point_type
                       | (point->extended_scanner_channel << 2)
                       | (point->extended_classification_flags << 4));
            rec[39] = point->extended_classification;
            rec[40] = (unsigned char)(point->extended_return_number
                       | (point->extended_number_of_returns << 4));
            memcpy(rec + 41, point->wave_packet, 23);   /* first 23 of 29 */
            hash = fnv1a(hash, rec, 64);
            hash = fnv1a(hash, point->wave_packet + 23, 6);
            if (num_extra_bytes > 0)
                hash = fnv1a(hash, point->extra_bytes, num_extra_bytes);
            continue;
        }

        fprintf(out,
            "%lld %d %d %d %u %u %u %u %u %u %u %u %u %d %u %u %llu "
            "%u %u %u %u %u %u %u %u %d %u %u %u",
            (long long)i,
            point->X, point->Y, point->Z,
            (unsigned)point->intensity,
            (unsigned)point->return_number,
            (unsigned)point->number_of_returns,
            (unsigned)point->scan_direction_flag,
            (unsigned)point->edge_of_flight_line,
            (unsigned)point->classification,
            (unsigned)point->synthetic_flag,
            (unsigned)point->keypoint_flag,
            (unsigned)point->withheld_flag,
            (int)point->scan_angle_rank,
            (unsigned)point->user_data,
            (unsigned)point->point_source_ID,
            gps_bits,
            (unsigned)point->rgb[0], (unsigned)point->rgb[1],
            (unsigned)point->rgb[2], (unsigned)point->rgb[3],
            (unsigned)point->extended_point_type,
            (unsigned)point->extended_scanner_channel,
            (unsigned)point->extended_classification_flags,
            (unsigned)point->extended_classification,
            (int)point->extended_scan_angle,
            (unsigned)point->extended_return_number,
            (unsigned)point->extended_number_of_returns,
            (unsigned)num_extra_bytes);

        for (int b = 0; b < 29; b++)
            fprintf(out, " %u", (unsigned)point->wave_packet[b]);
        for (int b = 0; b < num_extra_bytes; b++)
            fprintf(out, " %u", (unsigned)point->extra_bytes[b]);
        fprintf(out, "\n");
    }

    int format = header->point_data_format;
    if (hash_mode) {
        printf("%llu %lld\n", hash, (long long)limit);
    } else {
        fclose(out);
    }
    laszip_close_reader(reader);
    laszip_destroy(reader);
    fprintf(stderr, "read %lld points (format %d)\n", (long long)limit, format);
    return 0;
}
