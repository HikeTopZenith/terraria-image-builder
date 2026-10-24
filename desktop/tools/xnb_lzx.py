
"""LZX 解压器（XNB 容器用）。

算法移植自 libmspack 的 lzxd.c 与 MonoGame 的 LzxDecoder.cs（MS-PL / LGPL 双许可），
仅保留 XNB 解码所需路径。输出改为持续追加的 bytearray，等价于原实现的窗口语义。
"""

NUM_CHARS = 256
MIN_MATCH = 2
NUM_PRIMARY_LENGTHS = 7
NUM_SECONDARY_LENGTHS = 249
PRETREE_MAXSYMBOLS = 20
PRETREE_TABLEBITS = 6
MAINTREE_MAXSYMBOLS = NUM_CHARS + 50 * 8
MAINTREE_TABLEBITS = 12
LENGTH_MAXSYMBOLS = NUM_SECONDARY_LENGTHS + 1
LENGTH_TABLEBITS = 12
ALIGNED_MAXSYMBOLS = 8
ALIGNED_TABLEBITS = 7
LENTABLE_SAFETY = 64

BLOCK_INVALID, BLOCK_VERBATIM, BLOCK_ALIGNED, BLOCK_UNCOMPRESSED = 0, 1, 2, 3


class LzxError(Exception):
    pass


def _extra_bits_table():
    eb = [0] * 52
    j = 0
    for i in range(0, 51, 2):
        eb[i] = eb[i + 1] = j
        if i != 0 and j < 17:
            j += 1
    return eb


def _position_base_table(extra_bits):
    pb = [0] * 51
    j = 0
    for i in range(51):
        pb[i] = j
        j += 1 << extra_bits[i]
    return pb


EXTRA_BITS = _extra_bits_table()
POSITION_BASE = _position_base_table(EXTRA_BITS)


class BitReader:
    """MSB 优先的位读取器；越界补 0 并记 overrun 次数。"""

    __slots__ = ("data", "pos", "buf", "n", "overrun")

    def __init__(self, data, pos):
        self.data = data
        self.pos = pos
        self.buf = 0
        self.n = 0
        self.overrun = 0

    def refill(self, need):
        d = self.data
        end = len(d)
        while self.n < need:
            p = self.pos
            if p + 1 < end:
                self.buf = (self.buf << 16) | ((d[p + 1] << 8) | d[p])
            else:
                b1 = d[p] if p < end else 0
                b2 = d[p + 1] if p + 1 < end else 0
                self.buf = (self.buf << 16) | ((b2 << 8) | b1)
                self.overrun += 1
            self.pos = p + 2
            self.n += 16

    def peek(self, k):
        if self.n < k:
            self.refill(k)
        return (self.buf >> (self.n - k)) & ((1 << k) - 1)

    def skip(self, k):
        self.n -= k
        self.buf &= (1 << self.n) - 1

    def read(self, k):
        if k == 0:
            return 0
        v = self.peek(k)
        self.skip(k)
        return v

    def align16(self):
        """丢弃未用位并把流位置对齐到 16 位字边界。"""
        consumed_bits = (self.pos - 0) * 8 - self.n
        aligned = (consumed_bits + 15) // 16 * 16
        delta = aligned - consumed_bits
        if delta:
            self.skip(delta) if self.n >= delta else None
        self.pos = aligned // 8
        self.buf = 0
        self.n = 0


def make_decode_table(nsyms, nbits, lengths, table):
    pos = 0
    table_mask = 1 << nbits
    bit_mask = table_mask >> 1
    next_symbol = bit_mask
    bit_num = 1

    while bit_num <= nbits:
        for sym in range(nsyms):
            if lengths[sym] == bit_num:
                leaf = pos
                pos += bit_mask
                if pos > table_mask:
                    raise LzxError("huffman 表溢出（短码）")
                fill = bit_mask
                while fill > 0:
                    table[leaf] = sym
                    leaf += 1
                    fill -= 1
        bit_mask >>= 1
        bit_num += 1

    if pos != table_mask:
        for s in range(pos, table_mask):
            table[s] = 0
        pos <<= 16
        table_mask <<= 16
        bit_mask = 1 << 15
        while bit_num <= 16:
            for sym in range(nsyms):
                if lengths[sym] == bit_num:
                    leaf = pos >> 16
                    for fill in range(bit_num - nbits):
                        if table[leaf] == 0:
                            table[next_symbol << 1] = 0
                            table[(next_symbol << 1) + 1] = 0
                            table[leaf] = next_symbol
                            next_symbol += 1
                        leaf = table[leaf] << 1
                        if (pos >> (15 - fill)) & 1:
                            leaf += 1
                    table[leaf] = sym
                    pos += bit_mask
                    if pos > table_mask:
                        raise LzxError("huffman 表溢出（长码）")
            bit_mask >>= 1
            bit_num += 1

    if pos == table_mask:
        return
    for sym in range(nsyms):
        if lengths[sym] != 0:
            raise LzxError("huffman 表残缺")


def read_huff_sym(table, lengths, nsyms, nbits, br):
    if br.n < 16:
        br.refill(16)
    i = table[br.peek(nbits)]
    if i >= nsyms:
        k = nbits
        while True:
            k += 1
            if k > 16:
                raise LzxError("huffman 长码超过 16 位")
            i = table[(i << 1) | (br.peek(k) & 1)]
            if i < nsyms:
                break
        br.skip(k)
        return i
    ln = lengths[i]
    if ln == 0:
        raise LzxError("huffman 命中空码")
    br.skip(ln)
    return i


class LzxDecoder:
    def __init__(self, window_bits=16):
        if window_bits < 15 or window_bits > 21:
            raise LzxError("窗口位宽 %d 超出 15..21" % window_bits)
        if window_bits == 20:
            posn_slots = 42
        elif window_bits == 21:
            posn_slots = 50
        else:
            posn_slots = window_bits << 1

        self.window_bits = window_bits
        self.main_elements = NUM_CHARS + (posn_slots << 3)
        self.R0 = self.R1 = self.R2 = 1
        self.header_read = 0
        self.intel_filesize = 0
        self.block_type = BLOCK_INVALID
        self.block_length = 0
        self.block_remaining = 0
        self.intel_started = 0
        self.out = bytearray()

        self.PRETREE_len = [0] * (PRETREE_MAXSYMBOLS + LENTABLE_SAFETY)
        self.MAINTREE_len = [0] * (MAINTREE_MAXSYMBOLS + LENTABLE_SAFETY)
        self.LENGTH_len = [0] * (LENGTH_MAXSYMBOLS + LENTABLE_SAFETY)
        self.ALIGNED_len = [0] * (ALIGNED_MAXSYMBOLS + LENTABLE_SAFETY)
        self.PRETREE_table = [0] * ((1 << PRETREE_TABLEBITS) + (PRETREE_MAXSYMBOLS << 1))
        self.MAINTREE_table = [0] * ((1 << MAINTREE_TABLEBITS) + (MAINTREE_MAXSYMBOLS << 1))
        self.LENGTH_table = [0] * ((1 << LENGTH_TABLEBITS) + (LENGTH_MAXSYMBOLS << 1))
        self.ALIGNED_table = [0] * ((1 << ALIGNED_TABLEBITS) + (ALIGNED_MAXSYMBOLS << 1))

    def _read_lengths(self, lens, first, last, br):
        pre = self.PRETREE_len
        for x in range(20):
            pre[x] = br.read(4)
        make_decode_table(PRETREE_MAXSYMBOLS, PRETREE_TABLEBITS, pre, self.PRETREE_table)
        x = first
        while x < last:
            z = read_huff_sym(self.PRETREE_table, pre, PRETREE_MAXSYMBOLS, PRETREE_TABLEBITS, br)
            if z == 17:
                y = br.read(4) + 4
                while y > 0:
                    lens[x] = 0
                    x += 1
                    y -= 1
            elif z == 18:
                y = br.read(5) + 20
                while y > 0:
                    lens[x] = 0
                    x += 1
                    y -= 1
            elif z == 19:
                y = br.read(1) + 4
                z = read_huff_sym(self.PRETREE_table, pre, PRETREE_MAXSYMBOLS, PRETREE_TABLEBITS, br)
                z = lens[x] - z
                if z < 0:
                    z += 17
                while y > 0:
                    lens[x] = z
                    x += 1
                    y -= 1
            else:
                z = lens[x] - z
                if z < 0:
                    z += 17
                lens[x] = z
                x += 1
            if x > last:
                raise LzxError("码长表越界")

    def decompress(self, data, in_off, in_len, out_len):
        """解码一段（一帧），把结果追加到 self.out。"""
        br = BitReader(data, in_off)
        end = in_off + in_len

        out = self.out
        R0, R1, R2 = self.R0, self.R1, self.R2

        if self.header_read == 0:
            if br.read(1) != 0:
                i = br.read(16)
                j = br.read(16)
                self.intel_filesize = (i << 16) | j
                raise LzxError("LZX 流启用了 E8 变换（intel_filesize=%d），本实现不支持" % self.intel_filesize)
            self.header_read = 1

        togo = out_len
        while togo > 0:
            if self.block_remaining == 0:
                if self.block_type == BLOCK_UNCOMPRESSED:
                    if self.block_length & 1:
                        br.pos += 1
                    br.buf = 0
                    br.n = 0
                self.block_type = br.read(3)
                i = br.read(16)
                j = br.read(8)
                self.block_remaining = self.block_length = (i << 8) | j
                if self.block_type == BLOCK_ALIGNED:
                    for i in range(8):
                        self.ALIGNED_len[i] = br.read(3)
                    make_decode_table(ALIGNED_MAXSYMBOLS, ALIGNED_TABLEBITS,
                                      self.ALIGNED_len, self.ALIGNED_table)
                if self.block_type in (BLOCK_ALIGNED, BLOCK_VERBATIM):
                    self._read_lengths(self.MAINTREE_len, 0, 256, br)
                    self._read_lengths(self.MAINTREE_len, 256, self.main_elements, br)
                    make_decode_table(MAINTREE_MAXSYMBOLS, MAINTREE_TABLEBITS,
                                      self.MAINTREE_len, self.MAINTREE_table)
                    if self.MAINTREE_len[0xE8] != 0:
                        self.intel_started = 1
                    self._read_lengths(self.LENGTH_len, 0, NUM_SECONDARY_LENGTHS, br)
                    make_decode_table(LENGTH_MAXSYMBOLS, LENGTH_TABLEBITS,
                                      self.LENGTH_len, self.LENGTH_table)
                elif self.block_type == BLOCK_UNCOMPRESSED:
                    self.intel_started = 1
                    br.align16()
                    p = br.pos
                    R0 = int.from_bytes(data[p:p + 4], "little")
                    R1 = int.from_bytes(data[p + 4:p + 8], "little")
                    R2 = int.from_bytes(data[p + 8:p + 12], "little")
                    br.pos = p + 12
                else:
                    raise LzxError("非法块类型 %d" % self.block_type)

            if br.pos - br.n // 8 > end + 2:
                raise LzxError("压缩流越界")
            if br.overrun > 4 and (br.pos * 8 - br.n) > end * 8:
                raise LzxError("压缩流耗尽")

            while self.block_remaining > 0 and togo > 0:
                this_run = self.block_remaining
                if this_run > togo:
                    this_run = togo
                togo -= this_run
                self.block_remaining -= this_run

                if self.block_type == BLOCK_UNCOMPRESSED:
                    p = br.pos
                    if p + this_run > len(data):
                        raise LzxError("未压缩块越界")
                    out += data[p:p + this_run]
                    br.pos = p + this_run
                    br.buf = 0
                    br.n = 0
                    continue

                if self.block_type == BLOCK_VERBATIM:
                    main_table = self.MAINTREE_table
                    main_len = self.MAINTREE_len
                    len_table = self.LENGTH_table
                    len_lens = self.LENGTH_len
                    while this_run > 0:
                        e = read_huff_sym(main_table, main_len, MAINTREE_MAXSYMBOLS,
                                          MAINTREE_TABLEBITS, br)
                        if e < NUM_CHARS:
                            out.append(e)
                            this_run -= 1
                            continue
                        e -= NUM_CHARS
                        mlen = e & NUM_PRIMARY_LENGTHS
                        if mlen == NUM_PRIMARY_LENGTHS:
                            mlen += read_huff_sym(len_table, len_lens, LENGTH_MAXSYMBOLS,
                                                  LENGTH_TABLEBITS, br)
                        mlen += MIN_MATCH
                        moff = e >> 3
                        if moff > 2:
                            if moff != 3:
                                extra = EXTRA_BITS[moff]
                                moff = POSITION_BASE[moff] - 2 + br.read(extra)
                            else:
                                moff = 1
                            R2 = R1
                            R1 = R0
                            R0 = moff
                        elif moff == 0:
                            moff = R0
                        elif moff == 1:
                            moff = R1
                            R1 = R0
                            R0 = moff
                        else:
                            moff = R2
                            R2 = R0
                            R0 = moff
                        this_run -= mlen
                        _copy_match(out, moff, mlen)

                elif self.block_type == BLOCK_ALIGNED:
                    main_table = self.MAINTREE_table
                    main_len = self.MAINTREE_len
                    len_table = self.LENGTH_table
                    len_lens = self.LENGTH_len
                    al_table = self.ALIGNED_table
                    al_lens = self.ALIGNED_len
                    while this_run > 0:
                        e = read_huff_sym(main_table, main_len, MAINTREE_MAXSYMBOLS,
                                          MAINTREE_TABLEBITS, br)
                        if e < NUM_CHARS:
                            out.append(e)
                            this_run -= 1
                            continue
                        e -= NUM_CHARS
                        mlen = e & NUM_PRIMARY_LENGTHS
                        if mlen == NUM_PRIMARY_LENGTHS:
                            mlen += read_huff_sym(len_table, len_lens, LENGTH_MAXSYMBOLS,
                                                  LENGTH_TABLEBITS, br)
                        mlen += MIN_MATCH
                        moff = e >> 3
                        if moff > 2:
                            extra = EXTRA_BITS[moff]
                            moff = POSITION_BASE[moff] - 2
                            if extra > 3:
                                extra -= 3
                                moff += br.read(extra) << 3
                                moff += read_huff_sym(al_table, al_lens, ALIGNED_MAXSYMBOLS,
                                                      ALIGNED_TABLEBITS, br)
                            elif extra == 3:
                                moff += read_huff_sym(al_table, al_lens, ALIGNED_MAXSYMBOLS,
                                                      ALIGNED_TABLEBITS, br)
                            elif extra > 0:
                                moff += br.read(extra)
                            else:
                                moff = 1
                            R2 = R1
                            R1 = R0
                            R0 = moff
                        elif moff == 0:
                            moff = R0
                        elif moff == 1:
                            moff = R1
                            R1 = R0
                            R0 = moff
                        else:
                            moff = R2
                            R2 = R0
                            R0 = moff
                        this_run -= mlen
                        _copy_match(out, moff, mlen)
                else:
                    raise LzxError("非法块类型 %d" % self.block_type)

        self.R0, self.R1, self.R2 = R0, R1, R2
        return togo


def _copy_match(out, moff, mlen):
    if moff <= 0:
        raise LzxError("匹配偏移非法 %d" % moff)
    src = len(out) - moff
    if src < 0:
        raise LzxError("匹配偏移回溯越过输出起点 %d" % moff)
    if mlen <= moff:
        out += out[src:src + mlen]
    else:
        chunk = bytes(out[src:])
        out += (chunk * (mlen // moff + 1))[:mlen]
