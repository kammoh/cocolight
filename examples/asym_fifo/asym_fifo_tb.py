"""testbench for Asymmetric FIFO"""
# Author: Kamyar Mohajerani (kammoh@gmail.com)
# Copyright: 2022 (c) Kamyar Mohajerani
# License: Solderpad Hardware License v2.1 (SHL-2.1)
import os
from random import randint

import cocotb
from cocotb.triggers import RisingEdge

from cocolight import (
    DUT,
    ValidReadyTb,
    cocotest,
)
from cocolight.utils import concat_words, grouper, random_bits, is_pow_2

NUM_TV = int(os.environ.get("NUM_TV", 200))


@cocotest(skip=False)
async def test_enq_deq(dut: DUT, num_tests: int = NUM_TV, debug=False):
    debug |= bool(os.environ.get("DEBUG", False))
    tb = ValidReadyTb(dut, debug=debug)
    wr_driver = tb.driver("enq", data_suffix=("data"), stalls=(0, 3))
    rd_monitor = tb.monitor("deq", data_suffix=("data"), back_pressure=(0, 5))
    await tb.reset()
    # get bound parameters/generics from the simulator
    G_WR_W = tb.get_value("G_WR_W", int)
    G_RD_W = tb.get_value("G_RD_W", int)
    G_CAPACITY = tb.get_value("G_CAPACITY", int)
    G_BIGENDIAN = tb.get_value("G_BIGENDIAN", bool)

    tb.log.info(
        "[%s]\n G_WR_W:%d\n G_RD_W:%d\n G_CAPACITY:%d\n G_BIG_ENDIAN=%s\n num_tests:%d\n",
        str(dut),
        G_WR_W,
        G_RD_W,
        G_CAPACITY,
        G_BIGENDIAN,
        num_tests,
    )

    assert is_pow_2(G_RD_W) and is_pow_2(G_WR_W) and is_pow_2(G_CAPACITY)
    assert G_CAPACITY >= G_WR_W and G_CAPACITY >= G_RD_W

    RD_WR_RATIO = G_RD_W // G_WR_W
    WR_RD_RATIO = G_WR_W // G_RD_W

    for _ in range(num_tests):
        if G_RD_W >= G_WR_W:
            n = randint(1, 50) * RD_WR_RATIO
            inputs = [random_bits(G_WR_W) for _ in range(n)]
            expected_outputs = [
                concat_words(g, bigendian=G_BIGENDIAN)
                for g in grouper(inputs, RD_WR_RATIO)
            ]
        else:
            n = randint(1, 50) * WR_RD_RATIO
            expected_outputs = [random_bits(G_RD_W) for _ in range(n)]
            inputs = [
                concat_words(g, bigendian=G_BIGENDIAN)
                for g in grouper(expected_outputs, WR_RD_RATIO)
            ]

        driver_task = cocotb.start_soon(wr_driver.enqueue_seq(inputs))
        await rd_monitor.expect_seq(expected_outputs)
        await driver_task


@cocotest(skip=False)
async def test_fill(dut: DUT, num_tests: int = NUM_TV, debug=False):
    debug |= bool(os.environ.get("DEBUG", False))
    tb = ValidReadyTb(dut, debug=debug)
    wr_driver = tb.driver("enq", data_suffix=("data"), stalls=(0, 3))
    rd_monitor = tb.monitor("deq", data_suffix=("data"), back_pressure=(0, 5))
    await tb.reset()
    # get bound parameters/generics from the simulator
    G_WR_W = tb.get_value("G_WR_W", int)
    G_RD_W = tb.get_value("G_RD_W", int)
    G_CAPACITY = tb.get_value("G_CAPACITY", int)
    G_BIGENDIAN = tb.get_value("G_BIGENDIAN", bool)

    tb.log.info(
        "[%s]\n G_WR_W:%d\n G_RD_W:%d\n G_CAPACITY:%d\n G_BIG_ENDIAN=%s\n num_tests:%d\n",
        str(dut),
        G_WR_W,
        G_RD_W,
        G_CAPACITY,
        G_BIGENDIAN,
        num_tests,
    )

    assert is_pow_2(G_RD_W) and is_pow_2(G_WR_W) and is_pow_2(G_CAPACITY)
    assert G_CAPACITY >= G_WR_W and G_CAPACITY >= G_RD_W

    RD_WR_RATIO = G_RD_W // G_WR_W
    WR_RD_RATIO = G_WR_W // G_RD_W

    assert (
        dut.deq_valid.value == 0 and dut.enq_ready.value == 1
    ), "should be initially empty"

    for _ in range(num_tests):
        await RisingEdge(dut.clk)
        assert dut.deq_valid.value == 0, "should be empty"
        await RisingEdge(dut.clk)
        assert dut.deq_valid.value == 0, "should be still empty"

        if G_RD_W >= G_WR_W:
            # we can fit an extra word in the output register!
            cap_words = (G_CAPACITY + G_RD_W) // G_WR_W
            inputs = [random_bits(G_WR_W) for _ in range(cap_words)]
            expected_outputs = [
                concat_words(g, bigendian=G_BIGENDIAN)
                for g in grouper(inputs, RD_WR_RATIO)
            ]
        else:
            # number of words we can read after FIFO is full, no extra word can be pushed in
            cap_words = G_CAPACITY // G_RD_W
            expected_outputs = [random_bits(G_RD_W) for _ in range(cap_words)]
            inputs = [
                concat_words(g, bigendian=G_BIGENDIAN)
                for g in grouper(expected_outputs, WR_RD_RATIO)
            ]

        driver_task = cocotb.start_soon(wr_driver.enqueue_seq(inputs))
        await driver_task

        tb.log.debug("FIFO filled")

        await RisingEdge(dut.clk)
        assert dut.enq_ready.value == 0, "should be full"
        await RisingEdge(dut.clk)
        assert dut.enq_ready.value == 0, "should be still full"

        await rd_monitor.expect_seq(expected_outputs)

    await RisingEdge(dut.clk)
    assert dut.deq_valid.value == 0, "should be empty"
    await RisingEdge(dut.clk)
    assert dut.deq_valid.value == 0, "should be still empty"
