# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Make asammdf decode bus-logging frames as the application's decoder does.

A bus-logging MF4 is decoded by asammdf (``extract_bus_logging``), a BLF, ASC,
TRC or CSV by the application's own decoder through cantools. The same frames
must give the same signals and values. asammdf 8.8.27 differs in three ways:

* A signed 8-bit signal that does not start on a byte boundary is read as two
  bytes and never sign-extended: 0xFC gives 252, not -4.
* A J1939 signal whose every sample is above the J1939 valid range ("not
  available" or "error") is removed from the decoded file, so a DM1 lamp
  status reading 0xFF00 throughout has no signal at all. The application
  never applies asammdf's invalidation marks, so a J1939 signal with only
  some such samples keeps them all, as the other formats show them.
* A linear conversion with a negative factor and no offset turns raw 0 into
  -0.0, where the decoder gives 0.0 (an MDF3 file's as well).

``install()`` wraps the functions where asammdf looks them up, so every read
of a bus-logging MF4, eager, lazy or copied, gets the same values as the
other formats. Every other signal and conversion reaches asammdf unchanged.
"""

from __future__ import annotations

import functools

import numpy as np


def _original(function):
    """*function* before an earlier install() wrapped it."""
    return getattr(function, "__wrapped__", function)


def install() -> None:
    """Correct asammdf's bus-logging decode for the rest of the process.

    Idempotent: each wrapper keeps asammdf's own function, so installing
    twice does not wrap the wrappers.
    """
    try:
        from asammdf.blocks import bus_logging_utils, v2_v3_blocks, v4_blocks
        from asammdf.blocks import v2_v3_constants as v23c
        from asammdf.blocks import v4_constants as v4c
        from asammdf.blocks.utils import as_non_byte_sized_signed_int
    except ImportError:
        return  # each reader reports a missing asammdf itself

    extract_signal = _original(bus_logging_utils.extract_signal)

    @functools.wraps(extract_signal)
    def extract_signal_signed(signal, payload, raw=False,
                              ignore_value2text_conversion=True, is_ISOTP=False):
        values = extract_signal(signal, payload, True, ignore_value2text_conversion, is_ISOTP)
        # asammdf sign-extends any other size; a whole number of bytes it
        # only reinterprets, which is wrong once it has read one byte more.
        if (
            signal.is_signed and not signal.is_float and not is_ISOTP
            and signal.size in (8, 16, 32)
            and isinstance(values, np.ndarray) and values.dtype.kind == "i"
            and values.dtype.itemsize * 8 > signal.size
        ):
            values = as_non_byte_sized_signed_int(values, signal.size)
        if not raw:
            values = bus_logging_utils.apply_conversion(
                values, signal, ignore_value2text_conversion
            )
        return values

    extract_mux = _original(bus_logging_utils.extract_mux)

    @functools.wraps(extract_mux)
    def extract_mux_keeping_j1939(*args, **kwargs):
        # Without invalidation marks, extract_bus_logging keeps every J1939
        # signal. asammdf passes is_j1939 by keyword, here and in its readers.
        kwargs["is_j1939"] = False
        return extract_mux(*args, **kwargs)

    bus_logging_utils.extract_signal = extract_signal_signed
    bus_logging_utils.extract_mux = extract_mux_keeping_j1939

    for blocks, linear in (
        (v4_blocks, v4c.CONVERSION_TYPE_LIN),
        (v2_v3_blocks, v23c.CONVERSION_TYPE_LINEAR),
    ):
        blocks.ChannelConversion.convert = _without_negative_zero(
            _original(blocks.ChannelConversion.convert), linear
        )


def _without_negative_zero(convert, linear: int):
    """*convert*, giving 0.0 where a negative factor alone gives -0.0."""

    @functools.wraps(convert)
    def wrapper(self, values, *args, **kwargs):
        result = convert(self, values, *args, **kwargs)
        if self.conversion_type == linear and self.a < 0 and not self.b:
            if isinstance(result, np.ndarray) and result.dtype.kind == "f":
                if result is not values and result.flags.writeable:
                    result += 0.0
                else:
                    result = result + 0.0
            elif isinstance(result, np.floating):
                result = result + 0.0
        return result

    return wrapper
