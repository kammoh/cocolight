"""cocolight utils"""
from __future__ import annotations
from itertools import zip_longest
import random
from math import log2
from typing import Any, Iterable, List, Union
from cocotb.binary import BinaryValue
from cocotb.types import LogicArray, Range, Logic

__all__ = ["grouper", "concat_bitvectors", "concat_words", "UInt"]


def grouper(iterable: Iterable[Any], n: int, *, fill=None):
    """Collect data into non-overlapping fixed-length chunks or blocks"""
    args = [iter(iterable)] * n
    return zip(*args) if fill is None else zip_longest(*args, fillvalue=fill)


def concat_bitvectors(*bvs: Union[BinaryValue, LogicArray]) -> BinaryValue:
    # assert len(bvs) > 0
    # bvs = [bv.to_BinaryValue() if isinstance(bv, LogicArray) else bv for bv in bvs]
    # return reduce(lambda x, y: x.__concat__(y), map(LogicArray, bvs)).to_BinaryValue()
    return BinaryValue(
        "".join(bv.binstr for bv in bvs),
    )


def concat_bv(x: BinaryValue, y: BinaryValue) -> BinaryValue:
    return concat_bitvectors(x, y)


def concat_words(g, bigendian=True):
    g = list(g)
    if not bigendian:
        g = reversed(g)
    return concat_bitvectors(*g)


def random_bits(n: int) -> BinaryValue:
    return BinaryValue(value=random.getrandbits(n), n_bits=n)


def div_ceil(n: int, m: int) -> int:
    """n/m rounded up to next multiple of m"""
    return (n + m - 1) // m


def is_pow_2(n: int) -> bool:
    return 2 ** int(log2(n)) == n


def bytes_to_words(data: bytes, bit_width: int) -> List[BinaryValue]:
    """big endian"""
    return [
        concat_words(UInt(byte, 8) for byte in byte_group)
        for byte_group in grouper(
            data,
            bit_width // 8,
            fill=0,
        )
    ]


class UInt(BinaryValue):
    """
    Convenient wrapper for BinaryValue
    Rationale:
        cocotb's BinaryValue is utterly messed-up (to say the least)
        and LogicArray is not really a replacement
    """

    def __init__(
        self, val: Union[int, bool, str, Iterable[Logic], BinaryValue], n_bits=None
    ):
        width_range = (
            Range(n_bits - 1, "downto", 0)
            if isinstance(n_bits, int)
            else n_bits
            if isinstance(n_bits, Range)
            else None
        )
        if isinstance(val, (bool)):
            val = int(val)
        binstr = val if isinstance(val, str) else LogicArray(val, width_range).binstr
        super().__init__(value=binstr, n_bits=len(binstr), bigEndian=True)

    def __concat__(self, other) -> UInt:
        return UInt(self.binstr + other.binstr)

    def __rconcant__(self, other) -> UInt:
        return UInt(other.binstr + self.binstr)
