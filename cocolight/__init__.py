""""Lightweight cocotb testbench library"""
from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Coroutine
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Tuple,
    Type,
    TypeVar,
    Union,
)

import cocotb
from attrs import define
from box import Box
from cffi import FFI
from cocotb.binary import BinaryValue
from cocotb.clock import Clock
from cocotb.handle import ConstantObject, HierarchyObject, ModifiableObject
from cocotb.triggers import RisingEdge, Timer
from cocotb.types import LogicArray, concat

try:
    from typing import TypeAlias  # type: ignore
except ImportError:
    from typing_extensions import TypeAlias


DUT: TypeAlias = HierarchyObject

__version__ = "0.0.1"


class CocoTestCoroutine(Protocol):
    def __call__(
        self, dut: DUT, *args: Any, **kwargs: Any
    ) -> Coroutine[Any, Any, None]:
        ...


class CocoTestCoroutineX(Protocol):
    def __call__(self, dut: DUT, *args: Any) -> Coroutine[Any, Any, None]:
        ...


CT: TypeAlias = Union[CocoTestCoroutine, CocoTestCoroutineX]


def cocotest(
    f: Optional[CT] = None,
    /,
    *,
    timeout_time=None,
    timeout_unit="step",
    expect_fail=False,
    expect_error=(),
    skip=False,
    stage=None,
) -> Callable[
    [CT], Union[Coroutine[Any, Any, None], Awaitable[None]]
]:  # Union[Callable[[CT], Coroutine[Any, Any, None]], Coroutine[Any, Any, None], Any]:
    def wrap(coro: CT) -> Awaitable[None]:
        return cocotb.test(  # pylint: disable=E1120
            timeout_time=timeout_time,
            timeout_unit=timeout_unit,
            expect_fail=expect_fail,
            expect_error=expect_error,
            skip=skip,
            stage=stage,
        )(  # type: ignore
            coro,
        )

    if f is None:  # with parenthesis (and possibly arguments)
        return wrap
    return wrap(f)  # type: ignore


def concat_bv(x: BinaryValue, y: BinaryValue) -> BinaryValue:
    return concat(LogicArray(x), LogicArray(y)).to_BinaryValue()


def bv_repr(bv: BinaryValue) -> str:
    if not isinstance(bv, BinaryValue):
        return str(bv)
    try:
        if bv.n_bits:
            return f"0x{bv.integer:0{(bv.n_bits + 3) // 4}x}"
        return f"0x{bv.integer:x}"
    except ValueError:
        return bv.binstr


@define
class DutReset:
    port: str = "rst"
    active_high: bool = True
    synchronous: bool = True


@define
class DutClock:
    port: str = "clk"
    period: Tuple[float, str] = (10, "ns")


@define
class IoPort:  # data + valid + ready
    data_signal: Union[List[str], str]
    valid_signal: Optional[str] = None
    ready_signal: Optional[str] = None
    name: Optional[str] = None
    is_output: Optional[bool] = None

    # TODO use attr validators instead!
    def __attrs_post_init__(self) -> None:

        if not self.name and self.data_signal:
            self.name = (
                self.data_signal
                if isinstance(self.data_signal, str)
                else self.data_signal[0]
            )
        if self.name:
            if self.data_signal is None:
                self.data_signal = self.name
            if self.valid_signal is None:
                self.valid_signal = self.name + "_valid"
            if self.ready_signal is None:
                self.ready_signal = self.name + "_ready"
        else:
            self.name = uuid.uuid1().bytes.decode("utf-8")


PutableData = Union[int, str, bool, BinaryValue]


async def step_until(
    signal: ModifiableObject, clock_edge, trigger_value=1, timeout=None
):
    wait_counter = 0
    while True:
        await clock_edge
        if signal.value == trigger_value:
            break
        wait_counter += 1
        if timeout and wait_counter > timeout:
            raise ValueError(f"timed out after {timeout} cycles")


class ValidReadyInterface:
    def __init__(
        self,
        dut: DUT,
        clock: Union[str, ModifiableObject],
        prefix: str,
        data_suffix: Union[None, str, List[Union[str, None]]] = "data",
        sep: str = "_",
        timeout: Optional[int] = None,
        back_pressure: Union[None, Tuple[int, int], int] = None,
    ) -> None:
        self.dut = dut
        if isinstance(clock, str):
            clock = getattr(dut, clock)
        self.data_sig: Union[ModifiableObject, Dict[Union[str, None], ModifiableObject]]

        def _get_sig(ds):
            name = prefix if ds is None else prefix + sep + ds
            return getattr(dut, name)

        if data_suffix is None or isinstance(data_suffix, str):
            self.data_sig = _get_sig(data_suffix)
        elif isinstance(data_suffix, (Iterable, list)):
            self.data_sig = {ds: _get_sig(ds) for ds in data_suffix}
        else:
            raise TypeError(f"unsupported type for data_suffix: {type(data_suffix)}")

        self.clock = clock
        self.clock_edge = RisingEdge(clock)
        self.valid_sig: ModifiableObject = getattr(dut, prefix + sep + "valid")
        self.ready_sig: ModifiableObject = getattr(dut, prefix + sep + "ready")
        self.timeout: Optional[int] = timeout
        self.stall_min: int = 0
        self.stall_max: int = 0
        if back_pressure:
            if isinstance(back_pressure, tuple):
                assert len(back_pressure) == 2
                self.stall_min = back_pressure[0]
                self.stall_max = back_pressure[1]
            elif isinstance(back_pressure, int):
                self.stall_max = back_pressure

    async def wait_stalls(self):
        for _ in range(random.randrange(self.stall_min, self.stall_max)):
            await self.clock_edge


class ValidReadyDriver(ValidReadyInterface):
    def __init__(
        self,
        dut: DUT,
        clock,
        prefix: str,
        data_suffix: Union[None, str, List[Union[str, None]]] = "data",
        sep: str = "_",
        timeout: Optional[int] = None,
        back_pressure: Union[None, Tuple[int, int], int] = None,
    ) -> None:
        super().__init__(dut, clock, prefix, data_suffix, sep, timeout, back_pressure)
        self.valid_sig.setimmediatevalue(0)

    async def enqueue(self, data: Union[PutableData, dict[str, PutableData]]):
        clock_edge = RisingEdge(self.clock)
        await self.wait_stalls()
        self.valid_sig.value = 1
        if isinstance(self.data_sig, dict):
            assert isinstance(data, dict)
            for name, v in data.items():
                if not isinstance(v, (int, bool, BinaryValue)):
                    v = BinaryValue(v)
                self.data_sig[name].value = v
            # TODO put_rand ?
        else:
            assert isinstance(data, (int, str, bool, BinaryValue))
            self.data_sig.value = data
        await step_until(self.ready_sig, clock_edge, timeout=self.timeout)
        self.valid_sig.value = 0

    async def enqueue_seq(
        self, data: Iterable[Union[PutableData, dict[str, PutableData]]]
    ):
        for d in data:
            await self.enqueue(d)


class ValidReadyMonitor(ValidReadyInterface):
    def __init__(
        self,
        dut: DUT,
        clock,
        prefix: str,
        data_suffix: Union[None, str, List[Union[str, None]]] = "data",
        sep="_",
        timeout: Optional[int] = None,
        back_pressure: Union[None, Tuple[int, int], int] = None,
    ) -> None:
        super().__init__(dut, clock, prefix, data_suffix, sep, timeout, back_pressure)
        self.ready_sig.setimmediatevalue(0)

    async def dequeue(self) -> Union[BinaryValue, Box]:
        clock_edge = RisingEdge(self.clock)
        await self.wait_stalls()
        self.ready_sig.value = 1
        await step_until(self.valid_sig, clock_edge, timeout=self.timeout)
        if isinstance(self.data_sig, dict):
            data_dict = {}
            for name, sig in self.data_sig.items():
                data_dict[name] = sig.value
            ret = Box(data_dict)
        else:
            ret = self.data_sig.value
        self.ready_sig.value = 0
        return ret

    async def dequeue_seq(
        self, n: int
    ) -> List[Union[BinaryValue, Dict[str, BinaryValue]]]:
        return [await self.dequeue() for _ in range(n)]

    async def expect(self, expected):
        out = await self.dequeue()
        if isinstance(out, dict):
            assert isinstance(expected, dict), "output has multiple data fields"
            for name, v in expected.items():
                if not isinstance(v, (BinaryValue)):
                    v = BinaryValue(v)
                if out[name] != v:
                    raise ValueError(
                        f"Field {name} does not match! Received: {bv_repr(out[name])}, expected: {bv_repr(v)}"
                    )
        else:
            assert isinstance(out, BinaryValue)
            if not isinstance(expected, (BinaryValue)):
                expected = BinaryValue(expected)
            if out != expected:
                raise ValueError(f"out={bv_repr(out)}, expected={bv_repr(expected)}")

    async def expect_seq(self, values):
        for expected in values:
            await self.expect(expected)


T = TypeVar("T", int, bool, str, float, BinaryValue)


class LightTb:
    def __init__(
        self,
        dut: DUT,
        clock: DutClock = DutClock(),
        reset: DutReset = DutReset(),
        debug: bool = False,
    ):
        self.dut = dut
        self.log = logging.getLogger("cocotb_tb")
        self.log.setLevel(logging.DEBUG if debug else logging.INFO)
        self.debug = debug

        self.clock_cfg = clock
        self.reset_cfg = reset
        self.clock_sig = self.dut_attr(clock.port)
        self.reset_sig = self.dut_attr(reset.port)
        self.clock_edge = None
        self.clock_thread = None
        if self.clock_sig:
            self.clock_sig.setimmediatevalue(0)
            self.clock_edge = RisingEdge(self.clock_sig)
            (period, units) = clock.period
            self.clock_thread = cocotb.fork(
                Clock(self.clock_sig, period, units=units).start()
            )
        else:
            self.log.critical("No clocks found!")
        if self.reset_sig:
            self.reset_value = 1 if self.reset_cfg.active_high else 0
            self.reset_sig.setimmediatevalue(not self.reset_value)
        else:
            self.log.warning("No resets found. Specified reset signal: %s", reset.port)

    def dut_attr(self, attr):
        return getattr(self.dut, attr)

    def get_int_value(self, attr, otherwise=None):
        attr = getattr(self.dut, attr)
        if attr:
            return int(attr.value)
        return otherwise

    def get_value(self, attr, class_: Type[T]) -> T:
        bv = getattr(self.dut, attr)
        assert isinstance(
            bv, (ConstantObject, ModifiableObject)
        ), f"attr is of unexpected type {type(bv)}"
        v = bv.value
        return class_(v)

    async def _reset_sync(self, cycles=2, delay=None):
        if delay is None:
            delay = self.clock_cfg.period[0] / 2
        units = self.clock_cfg.period[1]
        await Timer(delay, units)
        self.reset_sig.value = self.reset_value
        if self.clock_edge:
            for _ in range(cycles):
                await self.clock_edge
        await Timer(delay, units)
        self.reset_sig.value = not self.reset_value

    async def _reset_async(self, duration=None):
        ...

    async def reset(self, **kwargs):
        if not self.reset_sig:
            return
        if self.reset_cfg.synchronous:
            await self._reset_sync()
        else:
            await self._reset_async()
        self.reset_sig._log.debug("Reset complete")  # pylint: disable=W0212


class ValidReadyTb(LightTb):
    def driver(
        self,
        prefix: str,
        data_suffix: Union[None, str, List[Union[None, str]]] = "data",
        sep: str = "_",
        timeout: Optional[int] = 1000,
        back_pressure=(0, 3),
        **kwargs,
    ) -> ValidReadyDriver:
        return ValidReadyDriver(
            self.dut,
            self.clock_sig,
            prefix,
            data_suffix=data_suffix,
            sep=sep,
            timeout=timeout,
            back_pressure=back_pressure,
            **kwargs,
        )

    def monitor(
        self,
        prefix: str,
        data_suffix: Union[None, str, List[Union[str, None]]] = "data",
        sep: str = "_",
        timeout: Optional[int] = 1000,
        back_pressure=(0, 5),
        **kwargs,
    ) -> ValidReadyMonitor:
        return ValidReadyMonitor(
            self.dut,
            self.clock_sig,
            prefix,
            data_suffix=data_suffix,
            sep=sep,
            timeout=timeout,
            back_pressure=back_pressure,
            **kwargs,
        )


class CModel:
    def __init__(self, sources) -> None:
        self.sources = sources
        self.lib = None
        self.ffi = None
        self.func_prototypes: List[str] = []

    def compile(self):

        ffibuilder = FFI()
        # name = 'trivium64'
        cdefs = "\n".join(self.func_prototypes)
        ffibuilder.cdef(cdefs)
        ffibuilder.set_source(
            f"_cmodel",
            cdefs,
            sources=self.sources,
            library_dirs=[],
            #  libraries = []
        )

        ffibuilder.compile(verbose=True, tmpdir=".")
        # from _cmodel import ffi, lib

        # self.ffi = ffi
        # self.lib = lib
