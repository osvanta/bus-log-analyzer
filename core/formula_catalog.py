# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
What the Insert Function window lists: every function of the calculated-signal
formula language, its comparison, logic and bitwise operators and its
constants, in the order a user meets them, each with its arguments in plain
words and an example that runs.

Functions come from core.calculated_signals._FUNCTIONS, so the window cannot
list a function the formula does not accept; the help for their arguments
lives here, and tests/test_formula_catalog.py checks that every function has it.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.calculated_signals import _CONSTANTS, _FUNCTIONS

#: Everyday first, specialised last. The Formula Help window uses it too.
CATEGORY_ORDER = (
    'Math', 'Rounding', 'Selection', 'Conditional', 'Comparison and logic',
    'Bitwise', 'Trigonometry', 'Time', 'Diagnostic', 'Constants',
)

#: The signal every example refers to.
EXAMPLE_SIGNAL = 'CH1::Engine::Speed'
_S = f'`{EXAMPLE_SIGNAL}`'

_X = 'a signal, a number or an expression'
_PLAIN = 'a signal from the picker; not an expression'
_ANGLE = 'an angle in radians'
_TIME_NOTE = ('The first argument must be a plain signal. For an expression, make it a '
              'calculated signal of its own first, then use that one here.')


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    category: str
    name: str                    # as written in a formula: round, IF, <=, AND, pi
    signature: str
    description: str
    arguments: tuple[tuple[str, str], ...]   # (name, what to give), every argument
    example: str
    note: str = ''
    keywords: str = ''           # extra words the search matches
    # What Insert writes: prefix, then the required arguments joined by
    # separator, then suffix. The arguments go in as placeholders to replace.
    prefix: str = ''
    params: tuple[str, ...] = ()
    separator: str = ', '
    suffix: str = ''

    @property
    def wraps_selection(self) -> bool:
        """Whether text selected in the formula becomes the first argument."""
        return bool(self.params)

    def insertion(self, selected: str = '') -> tuple[str, int, int]:
        """The text Insert writes, and the span in it to select afterwards:
        the first placeholder still to fill, or an empty span at its end.
        Selected formula text, if any, takes the first argument's place."""
        args = list(self.params)
        if selected and args:
            args[0] = selected
        text = self.prefix
        spans = []
        for i, arg in enumerate(args):
            if i:
                text += self.separator
            spans.append((len(text), len(text) + len(arg)))
            text += arg
        text += self.suffix
        target = 1 if selected and args else 0
        if target < len(spans):
            return (text, *spans[target])
        return text, len(text), len(text)

    def matches(self, needle: str) -> bool:
        needle = needle.strip().casefold()
        if not needle:
            return True
        haystack = ' '.join((self.name, self.signature, self.description, self.note,
                             self.keywords, self.category,
                             *(text for pair in self.arguments for text in pair)))
        return needle in haystack.casefold()


# name → (arguments, example, note, keywords). One entry per function in
# _FUNCTIONS, with as many arguments as it takes at most.
_FUNCTION_HELP: dict[str, tuple[tuple[tuple[str, str], ...], str, str, str]] = {
    'abs': ((('x', _X),), f'abs({_S} - 3000)', '', 'absolute magnitude'),
    'sqrt': ((('x', _X),), f'sqrt({_S})', 'A negative x gives NaN (a gap in the plot).',
             'square root'),
    'exp': ((('x', _X),), f'exp(-{_S} / 1000)', '', 'exponential'),
    'log': ((('x', _X),), f'log({_S})', 'Zero or a negative x gives NaN.', 'ln natural'),
    'log10': ((('x', _X),), f'20 * log10({_S})', 'Zero or a negative x gives NaN.',
              'decibel db'),
    'log2': ((('x', _X),), f'log2({_S})', 'Zero or a negative x gives NaN.', ''),
    'pow': ((('a', 'the base: ' + _X), ('b', 'the exponent: ' + _X)),
            f'pow({_S}, 2)', '', 'power square exponent'),
    'sign': ((('x', _X),), f'sign({_S} - 3000)', '', 'direction'),
    'mod': ((('a', 'the number divided: ' + _X), ('b', 'the divisor: ' + _X)),
            f'mod({_S}, 360)', 'The result takes the sign of b.', 'remainder modulo wrap'),
    'round': ((('x', _X), ('n', 'decimal places; optional, 0 if left out')),
              f'round({_S} / 3.6, 1)', '', 'decimals'),
    'floor': ((('x', _X),), f'floor({_S} / 1000)', '', 'round down integer'),
    'ceil': ((('x', _X),), f'ceil({_S} / 1000)', '', 'round up integer ceiling'),
    'trunc': ((('x', _X),), f'trunc({_S} / 1000)', '', 'truncate integer'),
    'min': ((('a', _X), ('b', _X)), f'min({_S}, 6000)', '', 'smaller minimum lower'),
    'max': ((('a', _X), ('b', _X)), f'max({_S}, 0)', '', 'larger maximum upper'),
    'clamp': ((('x', _X), ('lo', 'the lower limit'), ('hi', 'the upper limit')),
              f'clamp({_S}, 0, 6000)', '', 'limit saturate range'),
    'sin': ((('x', _ANGLE),), f'sin(radians({_S}))',
            'Use radians() to convert an angle in degrees.', 'sine'),
    'cos': ((('x', _ANGLE),), f'cos(radians({_S}))',
            'Use radians() to convert an angle in degrees.', 'cosine'),
    'tan': ((('x', _ANGLE),), f'tan(radians({_S}))',
            'Use radians() to convert an angle in degrees.', 'tangent'),
    'asin': ((('x', 'a value from -1 to 1'),), f'asin({_S} / 10000)',
             'Outside -1 to 1 gives NaN.', 'arcsine inverse'),
    'acos': ((('x', 'a value from -1 to 1'),), f'acos({_S} / 10000)',
             'Outside -1 to 1 gives NaN.', 'arccosine inverse'),
    'atan': ((('x', _X),), f'atan({_S})', '', 'arctangent inverse'),
    'atan2': ((('y', 'the y coordinate'), ('x', 'the x coordinate')),
              f'atan2({_S}, 1000)', 'Gives the angle in radians, from -pi to pi.',
              'arctangent angle heading'),
    'degrees': ((('x', _ANGLE),), f'degrees(atan2({_S}, 1000))', '', 'convert angle'),
    'radians': ((('x', 'an angle in degrees'),), f'radians({_S})', '', 'convert angle'),
    'IF': ((('condition', 'nonzero counts as true, for example a comparison'),
            ('then_value', 'the result where the condition is true'),
            ('else_value', 'the result everywhere else')),
           f'IF({_S} > 3000, 1, 0)', 'Write IF in capitals: lowercase if is not accepted.',
           'condition then else select switch'),
    'lag': ((('signal', _PLAIN), ('samples', 'how many samples back; a whole number, 0 or more')),
            f'{_S} - lag({_S}, 1)', _TIME_NOTE + ' Gives NaN until enough samples exist.',
            'previous earlier history'),
    'delay': ((('signal', _PLAIN), ('seconds', 'how far back in time, in seconds')),
              f'delay({_S}, 0.5)', _TIME_NOTE + ' Gives NaN until enough time has passed.',
              'shift time earlier'),
    'rolling_sum': ((('signal', _PLAIN),
                     ('samples', 'the window length in samples, the current one included; '
                                 '1 or more')),
                    f'rolling_sum({_S}, 10)',
                    _TIME_NOTE + ' Gives NaN until the window is full.', 'moving window total'),
    'rolling_mean': ((('signal', _PLAIN),
                      ('samples', 'the window length in samples, the current one included; '
                                  '1 or more')),
                     f'rolling_mean({_S}, 10)',
                     _TIME_NOTE + ' Gives NaN until the window is full. Also spelled '
                                  'rolling_avg.', 'average moving filter smooth'),
    'integral': ((('signal', _PLAIN),), f'integral({_S}) / 3600',
                 _TIME_NOTE + ' Starts at 0 and adds value × elapsed seconds.',
                 'sum area energy distance accumulate'),
    'derivative': ((('x', _PLAIN),), f'derivative({_S})',
                   _TIME_NOTE + ' The first sample gives NaN.', 'rate slope gradient change'),
    'diag': ((('event', 'a signal from the picker, nonzero while the event is present; '
                        'not an expression'),
              ('increment', 'added for each sample while the event is present'),
              ('decrement', 'taken off for each sample while it is not; never below 0')),
             f'diag({_S}, 2, 1) >= 10',
             'Compare the result with a limit, as in the example, to get a fault flag. '
             'For a condition such as a limit check, make it a calculated signal of its '
             'own first, then use that one as the event.',
             'debounce counter fault'),
}

_AB = (('a', _X), ('b', _X))
_COMPARISON_NOTE = '1 where it holds, 0 elsewhere.'
_BIT_NOTE = ('Works on whole numbers. Combined with a comparison, put each comparison in '
             'parentheses: (a > 0) & (b > 0).')


def _operator(category, name, description, example, note='', keywords='', unary=False):
    if unary:
        prefix = f'{name} (' if name.isalpha() else f'{name}('
        return CatalogEntry(category, name, f'{prefix}x)', description, (('x', _X),),
                            example, note, keywords, prefix=prefix, params=('x',), suffix=')')
    return CatalogEntry(category, name, f'a {name} b', description, _AB, example, note,
                        keywords, params=('a', 'b'), separator=f' {name} ')


_OPERATORS = (
    _operator('Comparison and logic', '<', 'a less than b', f'{_S} < 3000', _COMPARISON_NOTE,
              'less smaller below'),
    _operator('Comparison and logic', '<=', 'a less than or equal to b', f'{_S} <= 3000',
              _COMPARISON_NOTE, 'less equal'),
    _operator('Comparison and logic', '>', 'a greater than b', f'{_S} > 3000', _COMPARISON_NOTE,
              'greater larger above'),
    _operator('Comparison and logic', '>=', 'a greater than or equal to b', f'{_S} >= 3000',
              _COMPARISON_NOTE, 'greater equal'),
    _operator('Comparison and logic', '==', 'a equal to b', f'{_S} == 0', _COMPARISON_NOTE,
              'equal same'),
    _operator('Comparison and logic', '!=', 'a not equal to b', f'{_S} != 0', _COMPARISON_NOTE,
              'not equal different'),
    _operator('Comparison and logic', 'AND', 'True where both a and b are nonzero',
              f'({_S} > 1000) AND ({_S} < 3000)',
              '1 where both hold, 0 elsewhere. Also written and.', 'both'),
    _operator('Comparison and logic', 'OR', 'True where a or b, or both, is nonzero',
              f'({_S} < 1000) OR ({_S} > 3000)',
              '1 where either holds, 0 elsewhere. Also written or.', 'either'),
    _operator('Comparison and logic', 'NOT', 'True where x is zero', f'NOT ({_S} > 0)',
              '1 where x is zero, 0 elsewhere. Also written not.', 'negate invert', unary=True),
    _operator('Bitwise', '&', 'Bitwise AND: the bits set in both a and b', f'{_S} & 15',
              _BIT_NOTE, 'mask bits and'),
    _operator('Bitwise', '|', 'Bitwise OR: the bits set in a or b', f'{_S} | 1', _BIT_NOTE,
              'bits or set'),
    _operator('Bitwise', '^', 'Bitwise XOR: the bits set in exactly one of a and b',
              f'{_S} ^ 255', _BIT_NOTE, 'exclusive toggle bits'),
    CatalogEntry('Bitwise', '<<', 'x << n', 'Shift the bits of x left by n places',
                 (('x', _X), ('n', 'how many bit places')), f'{_S} << 2', _BIT_NOTE,
                 'shift left multiply', params=('x', 'n'), separator=' << '),
    CatalogEntry('Bitwise', '>>', 'x >> n', 'Shift the bits of x right by n places',
                 (('x', _X), ('n', 'how many bit places')), f'{_S} >> 2', _BIT_NOTE,
                 'shift right divide', params=('x', 'n'), separator=' >> '),
    _operator('Bitwise', '~', 'Bitwise NOT: every bit of x inverted', f'~({_S})',
              'x is taken as a 64-bit signed whole number, so ~0 is -1.', 'invert bits',
              unary=True),
)

_CONSTANT_ENTRIES = (
    CatalogEntry('Constants', 'pi', 'pi', 'The number π, 3.14159…', (),
                 f'2 * pi * {_S} / 60', keywords='circle', prefix='pi'),
    CatalogEntry('Constants', 'e', 'e', "Euler's number, 2.71828…", (),
                 f'pow(e, -{_S} / 1000)', keywords='euler exponential', prefix='e'),
)


def _function_entry(key: str) -> CatalogEntry:
    spec = _FUNCTIONS[key]
    arguments, example, note, keywords = _FUNCTION_HELP[spec.name]
    return CatalogEntry(
        spec.category, spec.name, spec.signature, spec.description, arguments, example,
        note, keywords, prefix=f'{spec.name}(',
        params=tuple(name for name, _help in arguments[:spec.min_args]), suffix=')')


def formula_catalog() -> list[CatalogEntry]:
    """Every entry, by CATEGORY_ORDER, each category in the order it is defined."""
    entries = [_function_entry(key) for key in _FUNCTIONS]
    entries += [*_OPERATORS, *(e for e in _CONSTANT_ENTRIES if e.name in _CONSTANTS)]
    return sorted(entries, key=lambda e: CATEGORY_ORDER.index(e.category))
