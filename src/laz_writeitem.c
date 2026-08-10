/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * Which writer codes which item, mirroring laz_readitem_new_raw and
 * make_compressed_reader on the read side.
 *
 * Both mappings sit here, rather than the compressed one living inside the
 * container as it does on the read side, so that the two can be read together
 * -- including the one deliberate asymmetry between the directions: the reader
 * accepts version 2 for the LAS 1.4 items, because lasproto once wrote it, and
 * the writer will not produce it.
 */
#include "laz_writeitem.h"
LazWriteItem *laz_writeitem_new_raw(const LazItem *item, LazOutStream *out)
{
    switch (item->type) {
    case LAZ_ITEM_POINT10:      return laz_writeitem_raw_point10(out);
    case LAZ_ITEM_GPSTIME11:    return laz_writeitem_raw_gpstime11(out);
    case LAZ_ITEM_RGB12:
    case LAZ_ITEM_RGB14:        return laz_writeitem_raw_rgb12(out);
    case LAZ_ITEM_RGBNIR14:     return laz_writeitem_raw_rgbnir14(out);
    case LAZ_ITEM_WAVEPACKET13:
    case LAZ_ITEM_WAVEPACKET14: return laz_writeitem_raw_wavepacket13(out);
    case LAZ_ITEM_BYTE:
    case LAZ_ITEM_BYTE14:       return laz_writeitem_raw_byte(out, item->size);
    case LAZ_ITEM_POINT14:      return laz_writeitem_raw_point14(out);
    default:                    return NULL;
    }
}

LazWriteItem *laz_writeitem_new_compressed(const LazItem *item, LazEncoder *enc)
{
    U32 v = item->version;

    switch (item->type) {
    case LAZ_ITEM_POINT10:
        if (v == 1) return laz_writeitem_v1_point10(enc);
        if (v == 2) return laz_writeitem_v2_point10(enc);
        return NULL;
    case LAZ_ITEM_GPSTIME11:
        if (v == 1) return laz_writeitem_v1_gpstime11(enc);
        if (v == 2) return laz_writeitem_v2_gpstime11(enc);
        return NULL;
    case LAZ_ITEM_RGB12:
        if (v == 1) return laz_writeitem_v1_rgb12(enc);
        if (v == 2) return laz_writeitem_v2_rgb12(enc);
        return NULL;
    case LAZ_ITEM_BYTE:
        if (v == 1) return laz_writeitem_v1_byte(enc, item->size);
        if (v == 2) return laz_writeitem_v2_byte(enc, item->size);
        return NULL;
    case LAZ_ITEM_WAVEPACKET13:
        /* wavepackets never got a v2 encoding, so the VLR of a v2 file still
         * declares this item as v1 -- as make_compressed_reader expects */
        if (v == 1) return laz_writeitem_v1_wavepacket13(enc);
        return NULL;
    /* The LAS 1.4 items exist only as layered writers. make_compressed_reader
     * also accepts version 2 for them, because lasproto once wrote that; there
     * is no reason to keep producing it. */
    case LAZ_ITEM_POINT14:
        if (v == 3) return laz_writeitem_v3_point14(enc);
        if (v == 4) return laz_writeitem_v4_point14(enc);
        return NULL;
    case LAZ_ITEM_RGB14:
        if (v == 3) return laz_writeitem_v3_rgb14(enc);
        if (v == 4) return laz_writeitem_v4_rgb14(enc);
        return NULL;
    case LAZ_ITEM_RGBNIR14:
        if (v == 3) return laz_writeitem_v3_rgbnir14(enc);
        if (v == 4) return laz_writeitem_v4_rgbnir14(enc);
        return NULL;
    case LAZ_ITEM_WAVEPACKET14:
        if (v == 3) return laz_writeitem_v3_wavepacket14(enc);
        if (v == 4) return laz_writeitem_v4_wavepacket14(enc);
        return NULL;
    case LAZ_ITEM_BYTE14:
        if (v == 3) return laz_writeitem_v3_byte14(enc, item->size);
        if (v == 4) return laz_writeitem_v4_byte14(enc, item->size);
        return NULL;
    default:
        return NULL;
    }
}
