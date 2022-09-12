"""testbench for SIPO"""
import os
import random
from functools import reduce
from typing import Tuple, Sequence, Optional

import cocotb
from cocotb.binary import BinaryValue
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

from cocolight import (
    DUT,
    ValidReadyTb,
    cocotest,
    Fork,
    ValidReadyDriver,
    ValidReadyMonitor,
    UInt,
    bv_repr,
)
from cocolight.utils import concat_bitvectors, concat_bv, grouper

NUM_TV = int(os.environ.get("NUM_TV", 10))


def random_bits(n: int) -> BinaryValue:
    # by default bigEndian=True
    return BinaryValue(value=random.getrandbits(n), n_bits=n)


def valid_bytes(w, total_bytes: Optional[int] = None, big_endian: bool = True) -> str:
    num_bytes = w // 8
    r = total_bytes % num_bytes if total_bytes else 0
    num_ones = num_bytes if r == 0 else r
    vb = ("0" * (num_bytes - num_ones)) + ("1" * num_ones)
    if big_endian:
        return vb[::-1]
    return vb


def div_ceil(n: int, m: int) -> int:
    """n/m rounded up to next multiple of m"""
    return (n + m - 1) // m


def gen_tv(
    in_type: type,
    out_type: type,
    in_width: int,
    n: int,
    with_subword: bool,
    bigendian: bool,
    num_channels: int,
    max_out_words: int,
) -> Tuple[Sequence, Sequence]:
    """generates a testvector
    @param in_width:      input bit-width
    @param n:             number of elements
    @param with_subword:  supports filling less than an output word, i.e., with keep and last
    @param bigendian:     input and output as big-endian (most significant byte/word at the lowest index)
    @param num_channels:  number of disjoint data channels (shares)
    @param max_out_words: max num parallel (output) words

    @return (input words, expected output words)
    """
    out_width = in_width * n

    def concat_words(g):
        return reduce(concat_bv, g if bigendian else reversed(g))

    if with_subword:
        num_data_bytes = random.randint(1, max_out_words * out_width // 8)
    else:
        num_data_bytes = random.randint(1, max_out_words) * out_width // 8
    # num_in_words = div_ceil(num_data_bytes, in_width // 8)
    # num_out_words = div_ceil(num_data_bytes, out_width // 8)
    # log.debug(
    #     "bytes:%d in_words:%d out_words:%d",
    #     num_data_bytes,
    #     num_in_words,
    #     num_out_words,
    # )
    data_byte_channels = [
        [random_bits(8) for _ in range(num_data_bytes)] for _ in range(num_channels)
    ]
    in_channels = [
        [
            concat_words(g)
            for g in grouper(
                data_bytes,
                in_width // 8,
                fill=BinaryValue(0, n_bits=8),
            )
        ]
        for data_bytes in data_byte_channels
    ]
    in_data = [
        in_type(  #
            data=concat_words(g),
            keep=valid_bytes(in_width, big_endian=bigendian),
            last=0,
        )
        for g in zip(*in_channels)
    ]
    # fix the last input
    in_data[-1].last = 1
    in_data[-1].keep = valid_bytes(in_width, num_data_bytes)

    expected_outputs_of_channel = [
        [
            concat_words(g)
            for g in grouper(
                data_bytes,
                out_width // 8,
                fill=BinaryValue(0, n_bits=8),
            )
        ]
        for data_bytes in data_byte_channels
    ]
    expected_outputs = [
        out_type(data=concat_words(per_channel), keep=valid_bytes(out_width), last=0)
        for per_channel in zip(*expected_outputs_of_channel)
    ]
    # fix the last output
    expected_outputs[-1].last = 1
    expected_outputs[-1].keep = valid_bytes(out_width, num_data_bytes)
    return in_data, expected_outputs


@cocotest(skip=True)
async def test_enq_deq(dut: DUT):
    sin_driver = ValidReadyDriver(
        dut, dut.clk, "sin", data_suffix=("data", "keep", "last")
    )
    pout_monitor = ValidReadyMonitor(
        dut, dut.clk, "pout", data_suffix=("data", "keep", "last")
    )

    await cocotb.start(Clock(dut.clk, 10).start())
    dut.rst.value = 1
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    n_channels = 1
    n = 4
    in_width = 8

    num_out_words = 2

    in_data = [
        concat_bitvectors(
            *(UInt(i + (j + 0xA) * 16, in_width) for j in range(n_channels))
        )
        for i in range(n * num_out_words)
    ]

    async with Fork() as fork:
        fork @ sin_driver.enqueue_seq(dict(data=d, keep=0, last=False) for d in in_data)
        out_words = await pout_monitor.dequeue_seq(num_out_words)

    expected_words = [reduce(concat_bitvectors, g) for g in grouper(in_data, n)]

    if n_channels == 1:
        for i, exp in enumerate(expected_words):
            assert (
                exp == out_words[i].data
            ), f"expected_words={bv_repr(exp)} != out_words={bv_repr(out_words[i].data)}"


@cocotest(skip=False)
async def test_sipo(dut: DUT, num_tests: int = NUM_TV, debug=False):
    debug |= bool(os.environ.get("DEBUG", False))
    tb = ValidReadyTb(dut, debug=debug)
    sin_driver = tb.driver("sin", data_suffix=("data", "keep", "last"))
    pout_monitor = tb.monitor("pout", data_suffix=("data", "keep", "last"))
    await tb.reset()
    # get bound parameters/generics from the simulator
    G_IN_W = tb.get_value("G_IN_W", int)
    G_N = tb.get_value("G_N", int)
    G_CHANNELS = tb.get_value("G_CHANNELS", int)
    G_ASYNC_RSTN = tb.get_value("G_ASYNC_RSTN", bool)
    G_PIPELINED = tb.get_value("G_PIPELINED", bool)
    G_BIGENDIAN = tb.get_value("G_BIGENDIAN", bool)
    G_SUBWORD = tb.get_value("G_SUBWORD", bool)
    G_CLEAR_INVALID_BYTES = tb.get_value("G_CLEAR_INVALID_BYTES", bool)

    tb.log.info(
        "[%s] G_IN_W:%d G_N:%d num_channels:%d G_ASYNC_RSTN:%s G_PIPELINED:%s G_BIGENDIAN:%s G_SUBWORD:%s "
        "G_CLEAR_INVALIDS:%s num_tests:%d",
        str(dut),
        G_IN_W,
        G_N,
        G_CHANNELS,
        G_ASYNC_RSTN,
        G_PIPELINED,
        G_BIGENDIAN,
        G_SUBWORD,
        G_CLEAR_INVALID_BYTES,
        num_tests,
    )

    for _ in range(num_tests):
        inputs, expected_outputs = gen_tv(
            sin_driver.DataType,
            pout_monitor.DataType,
            G_IN_W,
            G_N,
            with_subword=G_SUBWORD,
            bigendian=G_BIGENDIAN,
            num_channels=G_CHANNELS,
            max_out_words=10,
        )

        await FallingEdge(dut.clk)
        assert pout_monitor.valid.value != 1
        # driver_task = cocotb.start_soon(sin_driver.enqueue_seq(inputs))
        # await pout_monitor.expect_seq(expected_outputs)
        # await driver_task

        async with Fork(sin_driver.enqueue_seq(inputs)):
            await pout_monitor.expect_seq(expected_outputs)
