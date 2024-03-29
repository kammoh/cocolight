"""testbench for PISO"""
import os
import random
from functools import reduce
import cocotb
from cocotb.binary import BinaryValue
from cocolight import DUT, ValidReadyTb, cocotest, grouper
from cocolight.utils import concat_bitvectors, concat_bv

NUM_TV = int(os.environ.get("NUM_TV", 100))


def random_bits(n: int) -> BinaryValue:
    # by default bigEndian=True
    return BinaryValue(value=random.getrandbits(n), n_bits=n)


@cocotest
async def test_piso(dut: DUT, num_tests: int = NUM_TV, debug=False):
    tb = ValidReadyTb(dut, debug=debug)
    sin_driver = tb.driver("p_in", data_suffix=["data", "keep", "last"])
    pout_monitor = tb.monitor("s_out", data_suffix=["data", "keep", "last"])
    await tb.reset()
    # get bound parameters/generics from the simulator
    G_OUT_W = tb.get_value("G_OUT_W", int)
    G_N = tb.get_value("G_N", int)
    G_CHANNELS = tb.get_value("G_CHANNELS", int)
    G_ASYNC_RSTN = tb.get_value("G_ASYNC_RSTN", bool)
    G_BIGENDIAN = tb.get_value("G_BIGENDIAN", bool)
    G_SUBWORD = tb.get_value("G_SUBWORD", bool)

    def concat_words(g):
        return reduce(concat_bv, g if G_BIGENDIAN else reversed(g))

    tb.log.info(
        "[%s] G_OUT_W:%d G_N:%d G_CHANNELS:%d G_ASYNC_RSTN:%s G_BIGENDIAN:%s G_SUBWORD:%s num_tests:%d",
        str(dut),
        G_OUT_W,
        G_N,
        G_CHANNELS,
        G_ASYNC_RSTN,
        G_BIGENDIAN,
        G_SUBWORD,
        num_tests,
    )
    IN_WIDTH = G_OUT_W * G_N

    assert G_OUT_W % 8 == 0, "G_OUT_W should be multiple of bytes"

    M = 10  # max num parallel (input) words

    def div_ceil(n: int, m: int) -> int:
        return (n + m - 1) // m

    def valid_bytes(total_bytes, w, last=False) -> str:
        num_bytes = w // 8
        if last:
            r = total_bytes % num_bytes
            num_ones = num_bytes if r == 0 else r
        else:
            num_ones = num_bytes
        vb = ("0" * (num_bytes - num_ones)) + ("1" * num_ones)
        if G_BIGENDIAN:
            return vb[::-1]
        return vb

    for _test in range(num_tests):
        if G_SUBWORD:
            num_data_bytes = random.randint(1, M * IN_WIDTH // 8)
        else:
            # multiple of input number of bytes
            num_data_bytes = random.randint(1, M) * IN_WIDTH // 8

        num_in_words = div_ceil(num_data_bytes, IN_WIDTH // 8)
        num_out_words = div_ceil(num_data_bytes, G_OUT_W // 8)

        data_byte_channels = [
            [random_bits(8) for _ in range(num_data_bytes)] for _ in range(G_CHANNELS)
        ]
        expected_outputs_channels = [
            [
                concat_bitvectors(*g)
                for g in grouper(
                    data_bytes,
                    G_OUT_W // 8,
                    fill=BinaryValue(0, n_bits=8),
                )
            ]
            for data_bytes in data_byte_channels
        ]
        expected_outputs = [
            {
                "data": concat_words(per_channel),
                "keep": "1" * (G_OUT_W // 8),
                "last": 0,
            }  #
            for per_channel in zip(*expected_outputs_channels)
        ]

        in_data_channels = [
            [
                concat_words(g)
                for g in grouper(
                    data_byte,
                    IN_WIDTH // 8,
                    fill=BinaryValue(
                        0,
                        n_bits=data_byte[0].n_bits if data_byte else None,
                    ),
                )
            ]
            for data_byte in data_byte_channels
        ]

        in_data = [
            {
                "data": concat_words(per_channel),
                "keep": "1" * (IN_WIDTH // 8),
                "last": 0,
            }
            for per_channel in zip(*in_data_channels)
        ]

        in_data[-1]["last"] = 1
        in_data[-1]["keep"] = valid_bytes(num_data_bytes, IN_WIDTH, last=True)
        expected_outputs[-1]["last"] = 1
        expected_outputs[-1]["keep"] = valid_bytes(num_data_bytes, G_OUT_W, last=True)

        stimulus = cocotb.start_soon(sin_driver.enqueue_seq(in_data))

        await pout_monitor.expect_seq(expected_outputs)

        await stimulus  # join stimulus thread
