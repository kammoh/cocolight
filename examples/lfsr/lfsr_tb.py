import random

import cocotb
from cocolight import DUT, ValidReadyTb, cocotest


class LfsrTb(ValidReadyTb):
    async def send_seed(self):
        seed_driver = self.driver("rin", "data")
        rin_bits = self.dut.G_IN_BITS.value
        while True:
            await seed_driver.enqueue(random.getrandbits(rin_bits))


@cocotest
async def test_lfsr(dut: DUT):
    tb = LfsrTb(dut, "clk", "rst")
    OUT_BITS = dut.G_OUT_BITS.value
    await tb.reset()

    dut.reseed.value = 0

    await cocotb.start(tb.send_seed())
    out_monitor = tb.monitor("rout", "data")
    num_words = 100
    out = await out_monitor.dequeue_seq(num_words)

    assert len(out) == num_words
    print("out=", out)
