/*
 * mklaz -- generate LAS/LAZ test files covering every point data format and
 * every LASzip item version, using LASzip's internal API so the item version
 * can be forced (the public DLL always picks the default for the point type).
 *
 *   mklaz <point_type 0-10> <version 0-4> <npoints> <chunk_size> <out>
 *
 * version 0 writes an uncompressed LAS file; 1..4 request that item version.
 * A chunk_size of 0 selects the original POINTWISE container -- the whole file
 * as one stream with no chunk table -- which only exists for point types 0-5.
 *
 * The synthetic points are deliberately awkward: return numbers and counts
 * sweep their full range, intensities alternate between smooth ramps and
 * jumps, gps times mix constant, small-delta, multiplied-delta and
 * completely-different-sequence patterns, and classifications/user data churn.
 * That is what forces the coders down their rare branches.
 */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>

#include "laszip.hpp"
#include "laswritepoint.hpp"
#include "bytestreamout_file.hpp"

/* xorshift so the data is identical on every platform and every run */
static unsigned int rng_state = 2463534242u;
static unsigned int rnd()
{
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 17;
    rng_state ^= rng_state << 5;
    return rng_state;
}

static void put16(unsigned char* p, unsigned v) { p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; }
static void put32(unsigned char* p, unsigned v)
{
    p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; p[2] = (v >> 16) & 0xFF; p[3] = (v >> 24) & 0xFF;
}
static void putd(unsigned char* p, double v) { memcpy(p, &v, 8); }

int main(int argc, char** argv)
{
    if (argc < 6) {
        fprintf(stderr, "usage: mklaz <point_type 0-10> <version 0-4> <npoints> <chunk_size> <out>\n");
        return 1;
    }
    unsigned char point_type = (unsigned char)atoi(argv[1]);
    unsigned short version = (unsigned short)atoi(argv[2]);
    unsigned int npoints = (unsigned int)atoi(argv[3]);
    unsigned int chunk_size = (unsigned int)atoi(argv[4]);
    const char* out_path = argv[5];

    /* point record sizes for formats 0-10, plus 6 bytes of extra bytes so the
     * BYTE/BYTE14 readers are exercised too */
    static const unsigned short base_size[11] =
        { 20, 28, 26, 34, 57, 63, 30, 36, 38, 59, 67 };
    if (point_type > 10) { fprintf(stderr, "bad point type\n"); return 1; }
    const unsigned short extra_bytes = 6;
    unsigned short point_size = base_size[point_type] + extra_bytes;

    bool compressed = (version > 0);
    bool pointwise = compressed && chunk_size == 0;
    if (pointwise && point_type >= 6) {
        fprintf(stderr, "POINTWISE (chunk_size 0) predates point types 6-10\n");
        return 1;
    }

    unsigned short compressor = LASZIP_COMPRESSOR_NONE;
    if (compressed) {
        if (pointwise)              compressor = LASZIP_COMPRESSOR_POINTWISE;
        else if (point_type >= 6)   compressor = LASZIP_COMPRESSOR_LAYERED_CHUNKED;
        else                        compressor = LASZIP_COMPRESSOR_CHUNKED;
    }

    LASzip laszip;
    if (!laszip.setup(point_type, point_size, compressor)) {
        fprintf(stderr, "laszip setup failed: %s\n", laszip.get_error());
        return 1;
    }
    if (compressed) {
        if (!laszip.request_version(version)) {
            fprintf(stderr, "request_version(%u) failed: %s\n", version, laszip.get_error());
            return 1;
        }
        /* set_chunk_size refuses POINTWISE, which has no chunks to size */
        if (!pointwise && !laszip.set_chunk_size(chunk_size)) {
            fprintf(stderr, "set_chunk_size failed: %s\n", laszip.get_error());
            return 1;
        }
    }

    unsigned char* vlr_bytes = 0;
    int vlr_num = 0;
    if (compressed && !laszip.pack(vlr_bytes, vlr_num)) {
        fprintf(stderr, "laszip pack failed: %s\n", laszip.get_error());
        return 1;
    }

    /* LAS 1.4 for the extended point types, LAS 1.2 otherwise */
    bool las14 = (point_type >= 6);
    unsigned int header_size = las14 ? 375 : 227;
    unsigned int vlr_total = compressed ? (54 + (unsigned)vlr_num) : 0;
    unsigned int offset_to_point_data = header_size + vlr_total;

    unsigned char header[375];
    memset(header, 0, sizeof(header));
    memcpy(header, "LASF", 4);
    header[24] = 1;                                  /* version major */
    header[25] = las14 ? 4 : 2;                      /* version minor */
    memcpy(header + 26, "mklaz", 5);                 /* system identifier */
    memcpy(header + 58, "lazpy test generator", 20); /* generating software */
    put16(header + 90, 1);                           /* creation day */
    put16(header + 92, 2026);                        /* creation year */
    put16(header + 94, (unsigned)header_size);
    put32(header + 96, offset_to_point_data);
    put32(header + 100, compressed ? 1 : 0);         /* number of VLRs */
    header[104] = (unsigned char)(point_type | (compressed ? 0x80 : 0x00));
    put16(header + 105, point_size);
    put32(header + 107, las14 ? 0 : npoints);        /* legacy point count */
    putd(header + 131, 0.01);                        /* x scale */
    putd(header + 139, 0.01);
    putd(header + 147, 0.01);
    putd(header + 155, 0.0);                         /* x offset */
    putd(header + 163, 0.0);
    putd(header + 171, 0.0);
    putd(header + 179, 1.0e6);                       /* max x */
    putd(header + 187, -1.0e6);                      /* min x */
    putd(header + 195, 1.0e6);
    putd(header + 203, -1.0e6);
    putd(header + 211, 1.0e6);
    putd(header + 219, -1.0e6);
    if (las14) {
        put32(header + 235, 0);                      /* number of EVLRs */
        /* extended number of point records (8 bytes at 247) */
        put32(header + 247, npoints);
        put32(header + 251, 0);
    }

    FILE* f = fopen(out_path, "wb");
    if (!f) { fprintf(stderr, "cannot open %s\n", out_path); return 1; }
    fwrite(header, 1, header_size, f);

    if (compressed) {
        unsigned char vlr_header[54];
        memset(vlr_header, 0, sizeof(vlr_header));
        put16(vlr_header, 0);                                 /* reserved */
        memcpy(vlr_header + 2, "laszip encoded", 14);         /* user id */
        put16(vlr_header + 18, 22204);                        /* record id */
        put16(vlr_header + 20, (unsigned)vlr_num);
        memcpy(vlr_header + 22, "lazpy test", 10);            /* description */
        fwrite(vlr_header, 1, 54, f);
        fwrite(vlr_bytes, 1, (size_t)vlr_num, f);
    }

    ByteStreamOutFileLE outstream(f);
    LASwritePoint writer;
    if (!writer.setup(laszip.num_items, laszip.items, compressed ? &laszip : 0)) {
        fprintf(stderr, "writer setup failed\n");
        return 1;
    }
    if (!writer.init(&outstream)) { fprintf(stderr, "writer init failed\n"); return 1; }

    /*
     * Item buffers, laid out exactly as LASzip expects: POINT10/POINT14 write
     * into the combined struct at offset 0, GPSTIME11 at 32, RGB at 40,
     * WAVEPACKET at 48. A generous buffer covers all of them.
     */
    unsigned char point_buffer[256];
    memset(point_buffer, 0, sizeof(point_buffer));
    unsigned char extra_buffer[64];
    memset(extra_buffer, 0, sizeof(extra_buffer));

    unsigned char* items[8];
    for (unsigned int i = 0; i < laszip.num_items; i++) {
        switch (laszip.items[i].type) {
        case LASitem::POINT10:
        case LASitem::POINT14:      items[i] = point_buffer + 0; break;
        case LASitem::GPSTIME11:    items[i] = point_buffer + 32; break;
        case LASitem::RGB12:
        case LASitem::RGB14:
        case LASitem::RGBNIR14:     items[i] = point_buffer + 40; break;
        case LASitem::WAVEPACKET13:
        case LASitem::WAVEPACKET14: items[i] = point_buffer + 48; break;
        case LASitem::BYTE:
        case LASitem::BYTE14:       items[i] = extra_buffer; break;
        default: fprintf(stderr, "unexpected item type\n"); return 1;
        }
    }

    int x = 100000, y = 200000, z = 3000;
    double gps = 123456.789;
    double gps_diff = 0.0004;
    unsigned int intensity = 1000;

    for (unsigned int i = 0; i < npoints; i++) {
        /* coordinates: mostly smooth, with occasional jumps */
        x += (int)(rnd() % 41) - 20;
        y += (int)(rnd() % 41) - 20;
        z += (int)(rnd() % 11) - 5;
        if ((i % 977) == 0) { x += 50000; y -= 30000; z += 400; }

        unsigned int nret = 1 + (i % 7);
        unsigned int ret = 1 + (i % nret);

        if ((i % 13) == 0) intensity = rnd() % 65536;
        else intensity = (intensity + 7) & 0xFFFF;

        /* gps time: alternate between constant, small deltas, multiples of the
         * last delta, and an unrelated jump -- one branch each in the coder */
        switch ((i / 101) % 4) {
        case 0: break;                                  /* unchanged */
        case 1: gps += gps_diff; break;                 /* steady rate */
        case 2: gps += gps_diff * (1 + (i % 23)); break;/* multiples */
        case 3: if ((i % 307) == 0) gps += 100000.0 + (rnd() % 1000);
                else gps += gps_diff;
                break;
        }

        memset(point_buffer, 0, sizeof(point_buffer));
        memset(extra_buffer, 0, sizeof(extra_buffer));

        if (point_type >= 6) {
            /* combined struct: legacy fields plus the extended ones */
            put32(point_buffer + 0, (unsigned)x);
            put32(point_buffer + 4, (unsigned)y);
            put32(point_buffer + 8, (unsigned)z);
            put16(point_buffer + 12, intensity);
            unsigned int ret14 = 1 + (i % 15);
            unsigned int nret14 = ret14 + (i % 3);
            if (nret14 > 15) nret14 = 15;
            unsigned int legacy_ret = ret14 > 7 ? 7 : ret14;
            unsigned int legacy_nret = nret14 > 7 ? 7 : nret14;
            point_buffer[14] = (unsigned char)(legacy_ret | (legacy_nret << 3) |
                                               ((i & 1) << 6) | (((i >> 1) & 1) << 7));
            point_buffer[15] = (unsigned char)(i % 32);          /* legacy class */
            point_buffer[16] = (unsigned char)((int)(i % 61) - 30); /* scan angle rank */
            point_buffer[17] = (unsigned char)(i % 251);         /* user data */
            put16(point_buffer + 18, (unsigned)(i % 1009));      /* point source */
            put16(point_buffer + 20, (unsigned)(short)((int)(i % 8001) - 4000)); /* ext scan angle */
            point_buffer[22] = (unsigned char)(1 |                /* ext point type */
                                               (((i / 7) % 4) << 2) |   /* scanner channel */
                                               (((i / 3) % 16) << 4));  /* class flags */
            point_buffer[23] = (unsigned char)(i % 256);         /* ext classification */
            point_buffer[24] = (unsigned char)(ret14 | (nret14 << 4));
            memcpy(point_buffer + 32, &gps, 8);
        } else {
            put32(point_buffer + 0, (unsigned)x);
            put32(point_buffer + 4, (unsigned)y);
            put32(point_buffer + 8, (unsigned)z);
            put16(point_buffer + 12, intensity);
            point_buffer[14] = (unsigned char)(ret | (nret << 3) |
                                               ((i & 1) << 6) | (((i >> 1) & 1) << 7));
            point_buffer[15] = (unsigned char)(i % 32);
            point_buffer[16] = (unsigned char)((int)(i % 61) - 30);
            point_buffer[17] = (unsigned char)(i % 251);
            put16(point_buffer + 18, (unsigned)(i % 1009));
            memcpy(point_buffer + 32, &gps, 8);
        }

        /* rgb / nir */
        put16(point_buffer + 40, (unsigned)((i * 37) % 65536));
        put16(point_buffer + 42, (unsigned)((i * 37) % 65536) ^ ((i % 5) ? 0 : 0x1234));
        put16(point_buffer + 44, (unsigned)((i * 91) % 65536));
        put16(point_buffer + 46, (unsigned)((i * 13) % 65536));

        /* wavepacket: index, offset, size, return point, xyz */
        point_buffer[48] = (unsigned char)(i % 5);
        put32(point_buffer + 49, (unsigned)(i * 128));
        put32(point_buffer + 53, 0);
        put32(point_buffer + 57, 128);
        put32(point_buffer + 61, (unsigned)(i % 97));
        put32(point_buffer + 65, (unsigned)(i % 31));
        put32(point_buffer + 69, (unsigned)(i % 29));
        put32(point_buffer + 73, (unsigned)(i % 23));

        for (unsigned int b = 0; b < extra_bytes; b++)
            extra_buffer[b] = (unsigned char)((i * (b + 3)) % 256);

        if (!writer.write(items)) {
            fprintf(stderr, "write failed at point %u\n", i);
            return 1;
        }
    }

    if (!writer.done()) { fprintf(stderr, "writer done failed\n"); return 1; }
    fclose(f);

    fprintf(stderr, "wrote %s: type %u, version %u, %u points, size %u\n",
            out_path, point_type, version, npoints, point_size);
    return 0;
}
