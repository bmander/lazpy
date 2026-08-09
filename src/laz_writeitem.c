/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * Which writer codes which item, mirroring make_raw_reader and
 * make_compressed_reader in laz_readpoint.c.
 *
 * On the read side that mapping lives with the container, because the
 * container is the only caller. Here it sits with the writers instead: the
 * chunking container does not exist yet, and the tests that pin the writers
 * against laszip's own output have to go through the same mapping the
 * container will, or they stop pinning anything real.
 */
#include "laz_writeitem.h"
LazWriteItem *laz_writeitem_new_raw(const LazItem *item, LazOutStream *out)
{
    switch (item->type) {
    case LAZ_ITEM_POINT10:      return laz_writeitem_raw_point10(out);
    case LAZ_ITEM_GPSTIME11:    return laz_writeitem_raw_gpstime11(out);
    case LAZ_ITEM_RGB12:        return laz_writeitem_raw_rgb12(out);
    case LAZ_ITEM_WAVEPACKET13: return laz_writeitem_raw_wavepacket13(out);
    case LAZ_ITEM_BYTE:         return laz_writeitem_raw_byte(out, item->size);
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
    default:
        return NULL;
    }
}
