class ArithmeticBitModel:
    BM_LENGTH_SHIFT = 13
    BM_MAX_COUNT = 1 << BM_LENGTH_SHIFT

    def __init__(self):
        self.init()

    def init(self):
        # initialize equiprobable model
        self.bit_0_count = 1
        self.bit_count = 2
        self.bit_0_prob = 1 << (self.BM_LENGTH_SHIFT - 1)

        # start with frequent updates
        self.update_cycle = self.bits_until_update = 4

    def update(self):
        # halve counts when threshold is reached
        self.bit_count += self.update_cycle
        if self.bit_count >= self.BM_MAX_COUNT:
            self.bit_count = (self.bit_count + 1) >> 1
            self.bit_0_count = (self.bit_0_count + 1) >> 1
            if self.bit_0_count == self.bit_count:
                self.bit_count += 1

        # compute scaled bit 0 probability
        scale = 0x80000000 // self.bit_count
        self.bit_0_prob = (self.bit_0_count * scale) >> \
                          (31 - self.BM_LENGTH_SHIFT)

        # update frequency of model updates
        self.update_cycle = (5 * self.update_cycle) >> 2
        self.update_cycle = min(self.update_cycle, 64)
        self.bits_until_update = self.update_cycle

    def __repr__(self):
        return f'ArithmeticBitModel(update_cycle={self.update_cycle}, ' \
            f'bits_until_update={self.bits_until_update}, ' \
            f'bit_0_prob={self.bit_0_prob}, ' \
            f'bit_0Pcount={self.bit_0_count}, bit_count={self.bit_count})'


class ArithmeticModel:
    """An ArithmeticModel is a table of probabilities for a set of symbols."""
    DM_LENGTH_SHIFT = 15
    DM_MAX_COUNT = 1 << DM_LENGTH_SHIFT

    def __init__(self, num_symbols, compress):
        self.num_symbols = num_symbols
        self.compress = compress

        # tables
        self._distribution = None
        self._decoder_table = None
        self._symbol_count = None

    def init(self, table=None):
        if table is not None and len(table) != self.num_symbols:
            raise ValueError("Table size does not match number of symbols")

        if self._distribution is None:
            if self.num_symbols < 2 or self.num_symbols > 2048:
                raise Exception("Invalid number of symbols")

            self.last_symbol = self.num_symbols-1

            if not self.compress and self.num_symbols > 16:
                table_bits = 3
                while self.num_symbols > (1 << (table_bits+2)):
                    table_bits += 1

                self.table_shift = self.DM_LENGTH_SHIFT - table_bits

                self.table_size = 1 << table_bits
                self._decoder_table = [0] * (self.table_size + 2)
            else:  # small alphabet; no table needed
                self.table_shift = self.table_size = 0

            self._distribution = [0] * self.num_symbols
            self._symbol_count = [0] * self.num_symbols

        self.total_count = 0
        self.update_cycle = self.num_symbols
        if table is not None:
            self._symbol_count = table[:]
        else:
            self._symbol_count = [1] * self.num_symbols

        self._update()
        self.symbols_until_update = (self.num_symbols+6) >> 1
        self.update_cycle = self.symbols_until_update

    def _update(self):
        # halve counts when threshold is reached
        self.total_count += self.update_cycle
        if self.total_count > self.DM_MAX_COUNT:
            self.total_count = 0
            for i in range(self.num_symbols):
                self._symbol_count[i] = (self._symbol_count[i] + 1) >> 1
                self.total_count += self._symbol_count[i]

        # compute distribution
        sum, s = 0, 0
        scale = 0x80000000 // self.total_count

        if self.compress or self.table_size == 0:
            for k in range(self.num_symbols):
                # duplicate overflow math. interestingly, it doesn't seem to
                # affect the output, but it's nice to keep this close to the
                # c version
                big = (scale*sum) & 0xffffffff
                self._distribution[k] = big >> (31 - self.DM_LENGTH_SHIFT)
                sum += self._symbol_count[k]
        else:
            for k in range(self.num_symbols):
                self._distribution[k] = (scale*sum) >> \
                                       (31 - self.DM_LENGTH_SHIFT)
                sum += self._symbol_count[k]
                w = self._distribution[k] >> self.table_shift
                while s < w:
                    s += 1
                    self._decoder_table[s] = k-1
            self._decoder_table[0] = 0
            while s <= self.table_size:
                s += 1
                self._decoder_table[s] = self.num_symbols - 1


        # set frequency of model updates
        self.update_cycle = (5 * self.update_cycle) >> 2
        max_cycle = (self.num_symbols + 6) << 3
        self.update_cycle = min(self.update_cycle, max_cycle)
        self.symbols_until_update = self.update_cycle

    def increment_symbol_count(self, sym):
        self._symbol_count[sym] += 1
        self.symbols_until_update -= 1
        if self.symbols_until_update == 0:
            self._update()

        assert sym < self.num_symbols

    def decoder_table_lookup(self, ix):
        # for compatibility with the C version
        if self._decoder_table is None:
            raise Exception("No decoder table")

        return self._decoder_table[ix]

    def distribution_lookup(self, sym):
        # for compatibility with the C version
        if self._distribution is None:
            raise Exception("No distribution")

        return self._distribution[sym]

    def symbol_count_lookup(self, sym):
        # for compatibility with the C version
        if self._symbol_count is None:
            raise Exception("No symbol count")
            
        return self._symbol_count[sym]

    def has_decoder_table(self):
        return self._decoder_table is not None

    def __str__(self):
        ret = f"ArithmeticModel(num_symbols={self.num_symbols}," \
          f"compress={self.compress}, _distribution={self._distribution}, " \
          f"decoder_table={self._decoder_table}, " \
          f"symbol_count={self._symbol_count}, " \
          f"total_count={self.total_count}, " \
          f"update_cycle={self.update_cycle}, " \
          f"symbols_until_update={self.symbols_until_update})"

        return ret
