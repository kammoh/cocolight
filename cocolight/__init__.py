""""Lightweight cocotb testbench library"""
from __future__ import annotations

import decimal
import logging
import numbers
import random
from collections.abc import Coroutine
from typing import (
    Any,
    AsyncContextManager,
    Awaitable,
    Callable,
    Iterable,
    Optional,
    Protocol,
    Sequence,
    Type,
    TypeVar,
    Union,
)

import attrs
import cocotb
from attr import attrib
from attrs_strict import type_validator
from cocotb.binary import BinaryValue
from cocotb.clock import Clock
from cocotb.handle import ConstantObject, HierarchyObject, ModifiableObject
from cocotb.triggers import RisingEdge, Timer

try:
    from typing import TypeAlias  # type: ignore
except ImportError:
    from typing_extensions import TypeAlias  # python < 3.10

from .utils import UInt, grouper

__all__ = [
    "DUT",
    "LightTb",
    "ValidReadyInterface",
    "ValidReadyMonitor",
    "ValidReadyDriver",
    "ValidReadyTb",
    "grouper",
]

DUT: TypeAlias = HierarchyObject


class CocoTestCoroutine(Protocol):
    def __call__(
        self, dut: DUT, *args: Any, **kwargs: Any
    ) -> Coroutine[Any, Any, None]:
        ...


class CocoTestCoroutineX(Protocol):
    def __call__(self, dut: DUT, *args: Any) -> Coroutine[Any, Any, None]:
        ...


CT: TypeAlias = Union[
    CocoTestCoroutine,
    CocoTestCoroutineX,
    Callable[..., Coroutine[Any, Any, Any]],
]


def cocotest(
    f: Optional[CT] = None,
    /,
    *,
    timeout_time: Union[None, int, float, numbers.Real, decimal.Decimal] = None,
    timeout_unit: str = "step",
    expect_fail: bool = False,
    skip: bool = False,
    stage: Optional[int] = None,
) -> Callable[
    [CT], Union[Coroutine[Any, Any, None], Awaitable[None]]
]:  # Union[Callable[[CT], Coroutine[Any, Any, None]], Coroutine[Any, Any, None], Any]:
    def wrap(coro: CT) -> Awaitable[None]:
        return cocotb.test(  # pylint: disable=E1120
            timeout_time=timeout_time,
            timeout_unit=timeout_unit,
            expect_fail=expect_fail,
            skip=skip,
            stage=stage,
        )(  # type: ignore
            coro,
        )

    if f is None:  # with parenthesis (and possibly arguments)
        return wrap
    return wrap(f)  # type: ignore


def bv_repr(bv: BinaryValue) -> str:
    if not isinstance(bv, BinaryValue):
        return str(bv)
    try:
        if bv.n_bits:
            return f"0x{bv.integer:0{(bv.n_bits + 3) // 4}x}"
        return f"0x{bv.integer:x}"
    except ValueError:
        return bv.binstr


@attrs.define
class DutReset:
    port: str
    active_high: bool = True
    synchronous: bool = True


@attrs.define
class DutClock:
    port: str
    period: tuple[float, str] = (10.0, "ns")


BinaryValueType = Union[BinaryValue, int, str, bytes, bool]  # FIXME verify bool


async def step_until(
    signal: ModifiableObject, clock_edge, trigger_value=1, timeout=None
):
    wait_counter = 0
    while True:
        await clock_edge
        if signal.value == trigger_value:
            break
        if timeout:
            wait_counter += 1
            if wait_counter > timeout:
                raise ValueError(f"timed out after {timeout} cycles")


def to_binary_value(v: Any) -> BinaryValue:
    if isinstance(v, BinaryValue):
        return v
    return BinaryValue(v)


class ValidReadyInterface:
    """
    Abstraction of a bus with data (1 or more fields), valid, and ready signals
    """

    @attrs.define(slots=False)
    class DataType:
        pass

    def __init__(
        self,
        dut: DUT,
        clock: Union[str, ModifiableObject],
        prefix: str,
        data_suffix: Union[str, Sequence[str]] = "data",
        sep: str = "_",
        valid_signal: Optional[str] = None,
        ready_signal: Optional[str] = None,
        timeout: Optional[int] = None,
        back_pressure: Union[None, tuple[int, int], int] = None,
        debug: bool = False,
    ) -> None:
        self.dut = dut
        logger_name = f"{self.__class__.__name__}.{dut._name}"
        if prefix:
            logger_name += "." + prefix
        self.log = logging.getLogger(logger_name)
        if debug:
            self.log.setLevel(logging.DEBUG)
        self.debug = debug
        if isinstance(clock, str):
            clock = getattr(dut, clock)

        def _get_sig(suffix: str):
            # We assume signal/port name do not end with "_"
            # VHDL: they can't! Verilog/SystemVerilog: horrible practice!
            sig_name = prefix if not suffix else prefix + sep + suffix
            return getattr(dut, sig_name)

        self.data_sig: dict[str, ModifiableObject]
        assert data_suffix
        if isinstance(data_suffix, str):
            self.data_sig = {data_suffix: _get_sig(data_suffix)}
        elif isinstance(data_suffix, (Iterable, list)):
            # TODO: check no duplicates in data_suffix
            self.data_sig = {ds: _get_sig(ds) for ds in data_suffix}
        else:
            raise TypeError(f"unsupported type for data_suffix: {type(data_suffix)}")
        self.DataType = attrs.make_class(
            self.DataType.__name__,
            {
                s: attrib(validator=type_validator(), converter=UInt, type=BinaryValue)
                for s in self.data_sig
            },
            bases=(self.DataType,),
        )  # type: ignore
        self.clock = clock
        self.clock_edge = RisingEdge(clock)
        if not valid_signal:
            valid_signal = prefix + sep + "valid"
        if not ready_signal:
            ready_signal = prefix + sep + "ready"
        self.valid: ModifiableObject = getattr(dut, valid_signal)
        self.ready: ModifiableObject = getattr(dut, ready_signal)
        self.timeout = timeout
        self.stall_min: int = 0
        self.stall_max: int = 0
        if back_pressure is None:
            back_pressure = (0, 0)
        if back_pressure:
            if isinstance(back_pressure, tuple):
                assert len(back_pressure) == 2
                self.stall_min, self.stall_max = back_pressure
            else:
                assert isinstance(back_pressure, int)
                self.stall_max = back_pressure
        if self.stall_min < 0 or self.stall_max < 0 or self.stall_min > self.stall_max:
            raise ValueError(
                "Invalid backpressure specified: 0 <= min <= max is not true."
            )

    async def wait_stalls(self):
        if self.stall_max:
            for _ in range(random.randrange(self.stall_min, self.stall_max)):
                await self.clock_edge


PokeDict = dict[str, Union[str, int, BinaryValue]]


class ValidReadyDriver(ValidReadyInterface):
    """
    A valid/ready interface where the data is an input
    """

    def __init__(
        self,
        dut: DUT,
        clock,
        prefix: str,
        data_suffix: Union[str, Sequence[str]] = "data",
        sep: str = "_",
        valid_signal: Optional[str] = None,
        ready_signal: Optional[str] = None,
        timeout: Optional[int] = None,
        back_pressure: Union[None, tuple[int, int], int] = None,
        debug=False,
    ) -> None:
        super().__init__(
            dut,
            clock,
            prefix,
            data_suffix=data_suffix,
            sep=sep,
            valid_signal=valid_signal,
            ready_signal=ready_signal,
            timeout=timeout,
            back_pressure=back_pressure,
            debug=debug,
        )
        self.valid.setimmediatevalue(0)

    async def enqueue(
        self, data: Union[str, int, BinaryValue, PokeDict, ValidReadyDriver.DataType]
    ):
        await self.wait_stalls()
        self.valid.value = 1
        if isinstance(data, (int, str, BinaryValue)):
            data = {list(self.data_sig.keys())[0]: data}
        if isinstance(data, self.DataType):
            data = attrs.asdict(data)
        for name, v in data.items():
            if not isinstance(v, (int, bool, BinaryValue)):
                v = BinaryValue(v)
            self.data_sig[name].value = v
        await step_until(self.ready, self.clock_edge, timeout=self.timeout)
        self.valid.value = 0
        # TODO set data to rand/0/x?

    async def enqueue_seq(
        self,
        data_seq: Iterable[
            Union[str, int, BinaryValue, PokeDict, ValidReadyDriver.DataType]
        ],
    ):
        for data in data_seq:
            await self.enqueue(data)


class ValidReadyMonitor(ValidReadyInterface):
    """
    A valid/ready interface where the data signal(s) (and 'valid') are outputs
    """

    def __init__(
        self,
        dut: DUT,
        clock,
        prefix: str,
        data_suffix: Union[str, Sequence[str]] = "data",
        sep="_",
        valid_signal: Optional[str] = None,
        ready_signal: Optional[str] = None,
        timeout: Optional[int] = None,
        back_pressure: Union[None, tuple[int, int], int] = None,
    ) -> None:
        super().__init__(
            dut=dut,
            clock=clock,
            prefix=prefix,
            data_suffix=data_suffix,
            sep=sep,
            valid_signal=valid_signal,
            ready_signal=ready_signal,
            timeout=timeout,
            back_pressure=back_pressure,
        )
        self.ready.setimmediatevalue(0)

    async def dequeue(self) -> ValidReadyMonitor.DataType:
        clock_edge = RisingEdge(self.clock)
        await self.wait_stalls()
        self.ready.value = 1
        await step_until(self.valid, clock_edge, timeout=self.timeout)
        data_dict = {}
        for name, sig in self.data_sig.items():
            data_dict[str(name)] = sig.value
        self.ready.value = 0
        return self.DataType(**data_dict)

    async def dequeue_seq(self, num_words: int) -> list[ValidReadyMonitor.DataType]:
        """receive a sequence of data from this interface

        Args:
            num_words (int): number of data elements to receive

        Returns:
            list[Union[BinaryValue, dict[str, BinaryValue]]]: list of data words received
        """
        return [await self.dequeue() for _ in range(num_words)]

    async def expect(
        self,
        expected: Union[
            dict[str, Union[BinaryValue, str, int, bool]], ValidReadyMonitor.DataType
        ],
    ):
        out = attrs.asdict(await self.dequeue())
        # if isinstance(out, dict):
        # if not isinstance(expected, dict):
        #     raise TypeError(
        #         f"expected value must be a dictionary when the output port has multiple data fields ({out.keys()})."
        #     )
        if not isinstance(expected, dict):
            if isinstance(expected, (BinaryValue, str, int, bool)):
                if not isinstance(expected, (BinaryValue,)):
                    v = BinaryValue(expected)
                else:
                    v = expected
                if len(out) == 1:
                    sig = list(out.values())[0]
                    try:
                        failed = sig.value != v.value
                    except ValueError:
                        failed = sig.binstr != v.value
                    if failed:
                        # noinspection PyProtectedMember
                        raise ValueError(
                            f"Received: {bv_repr(sig)}  expected: {bv_repr(v)}"
                        )
                    return
            expected = attrs.asdict(expected)
        for name, v in expected.items():
            sig = out.get(name)
            if sig is None:
                raise ValueError(f"signal {name} not found!")
            if not isinstance(v, (BinaryValue,)):
                v = BinaryValue(v)
            try:
                failed = sig.value != v.value
            except ValueError:
                failed = sig.binstr != v.value
            if failed:
                # noinspection PyProtectedMember
                sig_name = self.data_sig[name]._name  # pylint: disable=protected-access
                raise ValueError(
                    f"Field '{name}' (signal: '{sig_name}') does not match the expected value!\n"
                    + f"  **  Received: {bv_repr(sig)}  expected: {bv_repr(v)} **"
                )

    async def expect_seq(self, seq: Iterable):
        for i, expected in enumerate(seq):
            try:
                await self.expect(expected)
            except ValueError as e:  # pylint: disable=invalid-name
                extra = (
                    f"/{len(seq)}" if isinstance(seq, (Sequence, list, tuple)) else ""
                )
                self.log.debug(
                    "Failed item #%d%s of the expected sequence", i + 1, extra
                )
                raise ValueError(
                    f"Failed item #{i + 1}{extra} of the expected sequence.\n  "
                    + e.args[0]
                )


T = TypeVar("T", int, bool, str, float, BinaryValue)


class LightTb:
    """generates main clock and reset for the DUT"""

    def __init__(
        self,
        dut: DUT,
        clock: Union[None, str, DutClock] = "clk",
        reset: Union[None, str, DutReset] = "rst",
        debug: bool = False,
    ):
        self.dut = dut
        self.log = logging.getLogger(f"{self.__class__.__name__}.{dut._name}")
        self.log.setLevel(logging.DEBUG if debug else logging.INFO)
        self.debug = debug

        if isinstance(clock, str):
            clock = DutClock(port=clock)
        if isinstance(reset, str):
            reset = DutReset(port=reset)

        self.clock_cfg = clock
        self.reset_cfg = reset
        self.clock_edge = None
        self.clock_thread = None
        self.clock_sig = None
        if self.clock_cfg is not None:
            self.clock_sig = self.dut_attr(self.clock_cfg.port)
            self.clock_sig.setimmediatevalue(0)
            self.clock_edge = RisingEdge(self.clock_sig)
            (period, units) = self.clock_cfg.period
            self.clock_thread = cocotb.start_soon(
                Clock(self.clock_sig, period, units=units).start()  # type: ignore
            )
        if self.reset_cfg is not None:
            self.reset_sig = self.dut_attr(self.reset_cfg.port)
            self.reset_value = 1 if self.reset_cfg.active_high else 0
            self.reset_sig.setimmediatevalue(not self.reset_value)

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
        ), f"attribute of unexpected type {type(bv)}"
        v = bv.value
        return class_(v)

    async def _reset_sync(self, cycles=2, delay: Union[None, float, int] = None):
        assert self.clock_cfg is not None, "clock must be set"
        if delay is None:
            delay = self.clock_cfg.period[0] / 2
        assert delay is not None
        units = self.clock_cfg.period[1]
        await Timer(delay, units)  # type: ignore
        self.reset_sig.value = self.reset_value
        if self.clock_edge:
            for _ in range(cycles):
                await self.clock_edge
        await Timer(delay, units)  # type: ignore
        self.reset_sig.value = not self.reset_value

    async def reset(self):
        if not self.reset_sig:
            self.log.warning("No reset signals were specified!")
            return
        self.log.debug("Starting reset...")
        await self._reset_sync()
        self.log.debug("Reset complete")


class ValidReadyTb(LightTb):
    """clock, reset"""

    def driver(
        self,
        prefix: str,
        data_suffix: Union[str, Sequence[str]] = "data",
        sep: str = "_",
        timeout: Optional[int] = 1000,
        stalls=None,
        **kwargs,
    ) -> ValidReadyDriver:
        assert self.clock_sig is not None, "clock should be set for a ValidReadyDriver"
        if "debug" not in kwargs:
            kwargs["debug"] = self.debug
        return ValidReadyDriver(
            self.dut,
            self.clock_sig,
            prefix,
            data_suffix=data_suffix,
            sep=sep,
            timeout=timeout,
            back_pressure=stalls,
            **kwargs,
        )

    def monitor(
        self,
        prefix: str,
        data_suffix: Union[str, Sequence[str]] = "data",
        sep: str = "_",
        timeout: Optional[int] = 1000,
        back_pressure=None,
    ) -> ValidReadyMonitor:
        return ValidReadyMonitor(
            self.dut,
            self.clock_sig,
            prefix,
            data_suffix=data_suffix,
            sep=sep,
            timeout=timeout,
            back_pressure=back_pressure,
        )


class Fork(AsyncContextManager):
    """
    fork abstraction
    can be used either as a ContextManager or a callable object
    """

    def __init__(self, *coroutines: Coroutine):
        self.tasks: list[Awaitable] = []
        for coro in coroutines:
            _ = self @ coro

    def __enter__(self):
        raise SyntaxError("You have to use 'async with Fork()'")

    def __exit__(self, exc_type, exc_val, exc_tb):
        raise SyntaxError("Should use async with")

    async def __aenter__(self):
        return self

    async def _join(self):
        rets = []
        while self.tasks:
            try:
                task = self.tasks.pop(0)
            except IndexError:
                break
            rets.append(await task)
        return None

    # def __await__(self):
    #     return (yield from self._join().__await__())

    async def __aexit__(
        self, exc_type: object, exc_val: object, exc_tb: object
    ) -> Optional[bool]:
        return await self._join()

    def __matmul__(self, coro: Coroutine) -> Fork:
        self.tasks.append(cocotb.start_soon(coro))
        return self

    def __rmatmul__(self, coro: Coroutine) -> Fork:
        return self @ coro
