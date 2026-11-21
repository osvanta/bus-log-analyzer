# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
A configuration that holds wrong values: a colour that is not a colour, a
number where a list belongs, a file that is not an object.

Whatever a configuration holds, loading it raises nothing. A wrong value is
left out and its default used, or the file is refused with a message before
anything of it is applied; nothing wrong reaches the plots, so nothing wrong
is saved again or kept as the last session. The same holds for File ▸ Open
configuration and for the last session offered at start.
"""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from gui import session_restore
from gui.session_restore import CloseAnswer, OfferAnswer

BAD_COLOUR = 'not-a-colour'


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """A settings folder of the test's own, with the last session on."""
    folder = tmp_path / 'settings'
    folder.mkdir()
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(folder))
    monkeypatch.setenv(session_restore.DISABLE_VARIABLE, '1')
    monkeypatch.setattr('sys.argv', ['app.py'])
    return folder


@pytest.fixture()
def boxes(monkeypatch):
    """Every message box, answered at once: Yes to keeping the current data."""
    from PySide6.QtWidgets import QMessageBox

    mocks = {}
    for name in ('information', 'warning', 'critical'):
        mocks[name] = Mock(return_value=QMessageBox.StandardButton.Ok)
        monkeypatch.setattr(QMessageBox, name, mocks[name])
    mocks['question'] = Mock(return_value=QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, 'question', mocks['question'])
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: 0)
    monkeypatch.setattr(session_restore, 'ask_to_save',
                        Mock(return_value=CloseAnswer.DISCARD))
    return mocks


@pytest.fixture()
def offered(monkeypatch):
    """Records each offer at start; the user answers Reopen."""
    offer = Mock(return_value=OfferAnswer.REOPEN)
    monkeypatch.setattr(session_restore, 'ask_to_reopen', offer)
    return offer


@pytest.fixture()
def uncaught(monkeypatch):
    """Errors raised where nothing catches them: in a slot or a timer."""
    seen: list[str] = []
    monkeypatch.setattr(
        sys, 'excepthook',
        lambda kind, error, trace: seen.append(f'{kind.__name__}: {error}'))
    return seen


@pytest.fixture()
def windows(qapp, settings, boxes):
    made = []

    def make(show: bool = False):
        from gui.main_window import MainWindow

        window = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
        window.resize(1200, 700)
        if show:
            window.show()
            qapp.processEvents()
        made.append(window)
        return window

    yield make
    for window in made:
        _wait_until_idle(window, qapp)
        window._session_restore = False
        window.close()
    qapp.processEvents()


def _wait_until_idle(window, qapp) -> None:
    deadline = time.monotonic() + 30.0
    while ((window._thread is not None or window._calc_thread is not None)
           and time.monotonic() < deadline):
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert window._thread is None and window._calc_thread is None, 'Work did not finish'


def _csv(path: Path, signals: int = 4, rows: int = 40) -> Path:
    header = 'Timestamps [s],' + ','.join(f'Sig{i} [V]' for i in range(signals))
    lines = [header] + [
        f'{row * 0.01:.3f},' + ','.join(str(row + i) for i in range(signals))
        for row in range(rows)
    ]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def _key(index: int) -> str:
    return f'CH?::CSV::Sig{index}'


def _good(measurement: Path, database: Path) -> dict:
    """A configuration as this version saves it: two tabs, a group, markers,
    a calculated signal, a database."""
    def signal(index: int, color: str, **changes) -> dict:
        return {'key': _key(index), 'color': color, 'visible': True, 'group': '',
                'axis_visible': True, 'own_axis': False, 'multistack_id': -1,
                'line_style': 'solid', **changes}

    first = [signal(0, '#e41a1c', group='Engine'), signal(1, '#377eb8', line_style='dash')]
    return {
        'version': '00.00.99',
        'measurement_path': str(measurement),
        'blf_path': str(measurement),
        'dbc_path': None,
        'channel_config': {'name': 'bench', 'channels': {'CAN:0': str(database)}},
        'signals': [{name: value for name, value in entry.items() if name != 'color'}
                    for entry in first],
        'generated_signals': [
            {'name': 'sum_one', 'formula': f'`{_key(0)}` + 1', 'unit': 'V'}],
        'step_lines': True,
        'show_data_points': False,
        'hide_plot_lines': False,
        'plot_background_color': '#101010',
        'signal_colors': {_key(0): '#e41a1c', _key(1): '#377eb8'},
        'group_colors': {'Engine': '#224466'},
        'multi_axis': False,
        'stacked': False,
        'multistack': False,
        'cursor1': True,
        'cursor2': False,
        'name_show_channel': False,
        'name_show_message': False,
        'table_column_widths': [40, 200, 95, 60, 58],
        'markers': [{'time': 0.1, 'name': 'start'}, {'time': 0.2, 'name': 'M2'}],
        'tabs': [
            {'name': 'Overview', 'plot_type': 'normal', 'show_data_points': False,
             'hide_plot_lines': False, 'cursor1': True, 'cursor2': False,
             'signals': first},
            {'name': 'Rows', 'plot_type': 'multistack', 'show_data_points': True,
             'hide_plot_lines': False, 'cursor1': False, 'cursor2': True,
             'signals': [signal(2, '#4daf4a', multistack_id=0),
                         signal(3, '#984ea3', multistack_id=1, visible=False)]},
        ],
        'current_tab': 1,
        'synchronize_tabs': False,
    }


@pytest.fixture()
def good(tmp_path, sample_dbc_path) -> dict:
    return _good(_csv(tmp_path / 'run.csv'), sample_dbc_path)


def _load(window, qapp, config: dict) -> None:
    """Decode the configuration's measurement, with nothing plotted."""
    window.measurement_path = window.blf_path = config['measurement_path']
    window.load_data()
    _wait_until_idle(window, qapp)
    assert window.store is not None


def _open(window, qapp, monkeypatch, tmp_path, content) -> None:
    """File ▸ Open configuration on a file holding *content*: JSON text, or
    anything else as JSON."""
    from PySide6.QtWidgets import QFileDialog

    path = tmp_path / 'opened.json'
    path.write_text(content if isinstance(content, str) else json.dumps(content),
                    encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
    window.load_configuration()
    _wait_until_idle(window, qapp)


def _colours(window) -> list:
    """Every colour the window would save."""
    config = window._configuration_dict()
    found = [config['plot_background_color'], *config['signal_colors'].values(),
             *config['group_colors'].values()]
    for tab in config['tabs']:
        found += [entry['color'] for entry in tab['signals']]
    return found


def _assert_sound(window) -> None:
    """What the window holds can be saved, and holds no wrong value."""
    from PySide6.QtGui import QColor
    import pyqtgraph as pg

    config = window._configuration_dict()
    json.dumps(config)
    for colour in _colours(window):
        assert isinstance(colour, str) and QColor(colour).isValid(), colour
        pg.mkColor(colour)
    for tab in config['tabs']:
        for entry in tab['signals']:
            assert isinstance(entry['multistack_id'], int)
            assert isinstance(entry['group'], str)
            assert all(isinstance(entry[name], bool)
                       for name in ('visible', 'axis_visible', 'own_axis'))
    assert all(isinstance(width, int) for width in config['table_column_widths'])
    assert all(isinstance(path, str) for path in config['channel_config']['channels'].values())


def _look(window) -> dict:
    """The configuration the window holds, less what a load does not set."""
    config = session_restore.as_saved(window._configuration_dict())
    return {name: value for name, value in config.items() if name != 'version'}


def _clear(window) -> None:
    for panel in window.plot_tabs.panels():
        panel.clear_all()


# ── The colour that is not a colour (issue 37) ───────────────────────────


def _with_bad_colour(good: dict) -> dict:
    bad = copy.deepcopy(good)
    bad['tabs'][0]['signals'][1]['color'] = BAD_COLOUR
    return bad


def test_an_unknown_colour_falls_back_to_the_default(
        qapp, monkeypatch, tmp_path, windows, good, uncaught):
    window = windows()
    _load(window, qapp, good)

    _open(window, qapp, monkeypatch, tmp_path, _with_bad_colour(good))
    qapp.processEvents()

    assert not uncaught
    panel = window.plot_tabs.panels()[0]
    assert panel.plotted_keys() == [_key(0), _key(1)]
    assert panel._items[_key(0)].color == '#e41a1c'
    assert BAD_COLOUR not in _colours(window)
    _assert_sound(window)


def test_an_unknown_colour_does_not_stick_to_the_next_load(
        qapp, monkeypatch, tmp_path, windows, good, uncaught):
    window = windows()
    _load(window, qapp, good)
    _open(window, qapp, monkeypatch, tmp_path, _with_bad_colour(good))

    _open(window, qapp, monkeypatch, tmp_path, good)
    qapp.processEvents()

    assert not uncaught
    assert window.plot_tabs.panels()[0]._items[_key(1)].color == '#377eb8'
    _assert_sound(window)


def test_an_unknown_colour_on_a_signal_already_plotted_is_ignored(
        qapp, monkeypatch, tmp_path, windows, good, uncaught):
    window = windows()
    _load(window, qapp, good)
    _open(window, qapp, monkeypatch, tmp_path, good)

    _open(window, qapp, monkeypatch, tmp_path, _with_bad_colour(good))
    qapp.processEvents()

    assert not uncaught
    assert window.plot_tabs.panels()[0]._items[_key(1)].color == '#377eb8'


def test_an_unknown_colour_is_not_kept_as_the_last_session(
        qapp, monkeypatch, tmp_path, settings, windows, good, offered, uncaught):
    window = windows()
    _load(window, qapp, good)
    _open(window, qapp, monkeypatch, tmp_path, _with_bad_colour(good))

    assert window.close()

    session = settings / session_restore.FILE_NAME
    assert BAD_COLOUR not in session.read_text(encoding='utf-8')
    again = windows(show=True)
    again._offer_last_session()
    _wait_until_idle(again, qapp)
    offered.assert_called_once()
    assert not uncaught
    assert again.plot_tabs.panels()[0].plotted_keys() == [_key(0), _key(1)]
    _assert_sound(again)


def test_a_last_session_holding_an_unknown_colour_reopens_and_is_kept_without_it(
        qapp, settings, windows, good, offered, uncaught):
    # As a version before this one left it.
    session = settings / session_restore.FILE_NAME
    session.write_text(json.dumps(_with_bad_colour(good)), encoding='utf-8')
    window = windows(show=True)

    window._offer_last_session()
    _wait_until_idle(window, qapp)

    assert not uncaught
    assert window.plot_tabs.panels()[0].plotted_keys() == [_key(0), _key(1)]
    _assert_sound(window)
    window._refresh_last_session()
    assert BAD_COLOUR not in session.read_text(encoding='utf-8')


def test_a_plot_refuses_a_colour_it_cannot_draw(qapp, windows, good, uncaught):
    window = windows()
    _load(window, qapp, good)
    window.add_signals_to_plot([_key(0)])
    panel = window.plot_panel
    before = panel._items[_key(0)].color

    panel.set_series_color(_key(0), BAD_COLOUR)
    panel.set_background_color(BAD_COLOUR)
    panel.set_pending_colors({_key(1): BAD_COLOUR})
    window.add_signals_to_plot([_key(1)])

    assert panel._items[_key(0)].color == before
    assert BAD_COLOUR not in _colours(window)
    _assert_sound(window)


# ── Wrong types (issue 38) ───────────────────────────────────────────────


def _changed(good: dict, change) -> dict:
    config = copy.deepcopy(good)
    change(config)
    return config


WRONG_TYPES = {
    'a column width that is no number': lambda c: c.update(
        table_column_widths=[120, 'wide', 80]),
    'column widths that are no list': lambda c: c.update(table_column_widths=7),
    'a multistack row that is null': lambda c: c['tabs'][1]['signals'][0].update(
        multistack_id=None),
    'a background that is a number': lambda c: c.update(plot_background_color=5),
    'group colours that are a number': lambda c: c.update(group_colors=1.5),
    'a group colour that is no colour': lambda c: c.update(
        group_colors={'Engine': BAD_COLOUR}),
    'a channel key that is no channel': lambda c: c['channel_config'].update(
        channels={'a': 'x.dbc'}),
    'channels that are a list': lambda c: c['channel_config'].update(channels=[1, 2]),
    'a database path that is a number': lambda c: c['channel_config'].update(
        channels={'CAN:1': 5}),
    'tabs that are a number': lambda c: c.update(tabs=3),
    'a tab name that is a list': lambda c: c['tabs'][0].update(name=[1]),
    'signals that are text': lambda c: c['tabs'][0].update(signals='abc'),
    'a signal key that is a number': lambda c: c['tabs'][0]['signals'][0].update(key=5),
    'a current tab that is infinite': lambda c: c.update(current_tab=float('inf')),
    'a multistack row that is infinite': lambda c: c['tabs'][1]['signals'][0].update(
        multistack_id=float('inf')),
    'a column width that is huge': lambda c: c.update(table_column_widths=[10 ** 30]),
    'markers that are a number': lambda c: c.update(markers=4),
    'calculated signals that are a number': lambda c: c.update(generated_signals=9),
    'a line style that is a list': lambda c: c['tabs'][0]['signals'][0].update(
        line_style=['dash']),
    'a group that is a number': lambda c: c['tabs'][0]['signals'][0].update(group=12),
}


@pytest.mark.parametrize('name', WRONG_TYPES)
def test_a_wrong_type_is_ignored_and_the_rest_applied(
        qapp, monkeypatch, tmp_path, windows, good, uncaught, boxes, name):
    window = windows()
    _load(window, qapp, good)

    _open(window, qapp, monkeypatch, tmp_path, _changed(good, WRONG_TYPES[name]))
    qapp.processEvents()

    assert not uncaught
    boxes['critical'].assert_not_called()
    _assert_sound(window)
    # What the file names after the wrong value is applied too.
    assert [marker.name for marker in window.plot_panel.markers()] == (
        [] if 'markers' in name else ['start', 'M2'])
    if 'tabs' not in name:
        assert window.plot_tabs.names() == ['Overview', 'Rows'] or 'tab name' in name
        assert window.plot_tabs.panels()[1].plotted_keys() == [_key(2), _key(3)]
    # And a good configuration afterwards loads as it always does.
    _clear(window)
    _open(window, qapp, monkeypatch, tmp_path, good)
    assert not uncaught
    assert window.plot_tabs.panels()[0].plotted_keys() == [_key(0), _key(1)]
    _assert_sound(window)


def test_a_bad_column_width_keeps_the_others(
        qapp, monkeypatch, tmp_path, windows, good, uncaught):
    window = windows()
    _load(window, qapp, good)
    before = window.plot_panel.table_column_widths()

    _open(window, qapp, monkeypatch, tmp_path,
          _changed(good, lambda c: c.update(table_column_widths=[120, 'wide', 0, 0, 77])))

    widths = window.plot_panel.table_column_widths()
    assert (widths[0], widths[1], widths[4]) == (120, before[1], 77)


def test_a_bad_channel_key_keeps_the_other_channels(
        qapp, monkeypatch, tmp_path, windows, good, sample_dbc_path, uncaught):
    from core.bus_types import BusType

    window = windows()
    _load(window, qapp, good)

    _open(window, qapp, monkeypatch, tmp_path, _changed(
        good, lambda c: c['channel_config']['channels'].update(a='x.dbc')))

    assert window.channel_config.channels == {(BusType.CAN, 0): str(sample_dbc_path)}


NO_CONFIGURATIONS = {
    'a list': '[]',
    'a number': '5',
    'text': '"text"',
    'null': 'null',
    'not JSON': '{"signals": [',
    'nested 100,000 deep': '[' * 100_000 + ']' * 100_000,
    'a key nested 100,000 deep': '{"tabs": ' + '[' * 100_000 + ']' * 100_000 + '}',
}


@pytest.mark.parametrize('content', NO_CONFIGURATIONS.values(), ids=list(NO_CONFIGURATIONS))
def test_a_file_that_is_no_configuration_is_refused_with_nothing_applied(
        qapp, monkeypatch, tmp_path, windows, good, uncaught, boxes, content):
    window = windows()
    _load(window, qapp, good)
    _open(window, qapp, monkeypatch, tmp_path, good)
    before = _look(window)
    held = (window._config_path, window._config_snapshot, window.channel_config)

    _open(window, qapp, monkeypatch, tmp_path, content)
    qapp.processEvents()

    assert not uncaught
    boxes['critical'].assert_called_once()
    title, reason = boxes['critical'].call_args.args[1:3]
    assert title == 'Load configuration failed'
    assert reason.strip()
    assert _look(window) == before
    assert (window._config_path, window._config_snapshot, window.channel_config) == held


LAST_SESSIONS = {
    'a list': '[]',
    'nested 100,000 deep': '[' * 100_000 + ']' * 100_000,
    'a key nested 100,000 deep': '{"signals": ' + '[' * 100_000 + ']' * 100_000 + '}',
    'not JSON': '{"signals": [',
    'empty': '',
    'a measurement that is a number': '{"measurement_path": 5, "signals": [{"key": "a"}]}',
    'a measurement that is a list': '{"measurement_path": ["a"], "blf_path": {"b": 1}}',
    'tabs that are a number': '{"measurement_path": "a.csv", "tabs": 5}',
    'signals that are a number': '{"measurement_path": "a.csv", "signals": 5}',
    'a tab whose signals are a number': '{"tabs": [{"signals": 5}]}',
    'where it was saved is a list': '{"measurement_path": "a.csv", "last_session": [1]}',
    'saved as a number': '{"measurement_path": "a.csv", "last_session": {"saved_to": 5}}',
    'every value null': json.dumps(dict.fromkeys(_good(Path('a.csv'), Path('b.dbc')))),
    'every value a number': json.dumps(
        dict.fromkeys(_good(Path('a.csv'), Path('b.dbc')), 1e400)),
}


@pytest.mark.parametrize('name', LAST_SESSIONS)
def test_the_application_starts_whatever_the_last_session_holds(
        qapp, settings, windows, offered, uncaught, name):
    (settings / session_restore.FILE_NAME).write_text(LAST_SESSIONS[name], encoding='utf-8')
    window = windows(show=True)

    window._offer_last_session()
    _wait_until_idle(window, qapp)
    qapp.processEvents()

    assert not uncaught
    _assert_sound(window)
    assert window.store is None


# ── Every value in turn made wrong ───────────────────────────────────────

_WRONG = (None, 12345, -1.5, float('inf'), BAD_COLOUR, '', True, [], [BAD_COLOUR], {},
          {'key': BAD_COLOUR})
_REMOVED = object()


def _paths(value, path=()):
    """Every value in a configuration, containers included, by its path."""
    if path:
        yield path
    if isinstance(value, dict):
        for name, inner in value.items():
            yield from _paths(inner, path + (name,))
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _paths(inner, path + (index,))


def _mutated(config: dict, path: tuple, wrong):
    mutated = copy.deepcopy(config)
    holder = mutated
    for step in path[:-1]:
        holder = holder[step]
    if wrong is _REMOVED:
        del holder[path[-1]]
    else:
        holder[path[-1]] = wrong
    return mutated


def _mutations(config: dict, top: str | None = None, each: int | None = None):
    """Copies of *config*, each with one value replaced by a value of another
    type, or removed: every wrong value for every value in turn, or, with
    *each*, that many wrong values per value, a different pick for the next.
    With *top*, only the values under that key."""
    for number, path in enumerate(p for p in _paths(config) if top in (None, p[0])):
        holder = config
        for step in path:
            holder = holder[step]
        wrong = [value for value in _WRONG if type(value) is not type(holder)]
        if each is not None:
            wrong = [wrong[(number + turn) % len(wrong)] for turn in range(each)]
        for value in (*wrong, _REMOVED):
            yield (f'{"/".join(map(str, path))} = '
                   f'{"removed" if value is _REMOVED else repr(value)}',
                   _mutated(config, path, value))


_TOP_KEYS = sorted(_good(Path('a.csv'), Path('b.dbc')))


def test_a_good_configuration_comes_back_equal(good):
    from gui import configuration

    assert configuration.normalised(copy.deepcopy(good)) == good
    assert configuration.parse(json.dumps(good)) == good


def test_keys_of_a_later_version_are_passed_through(good):
    from gui import configuration

    later = {**good, 'version': '99.00.00', 'new_setting': {'depth': [1, {'a': None}]}}
    later['tabs'][0]['new_tab_setting'] = [1, 2]
    later['tabs'][0]['signals'][0]['new_look'] = 'dotted-wide'

    assert configuration.normalised(copy.deepcopy(later)) == later


def test_a_file_saved_before_tabs_keeps_its_bare_signal_keys():
    from gui import configuration

    old = {'blf_path': 'a.blf', 'dbc_path': 'a.dbc', 'signals': ['CH1::M::S', {'key': 'k'}],
           'signal_colors': {'CH1::M::S': '#ff0000'}, 'stacked': True}

    assert configuration.normalised(copy.deepcopy(old)) == old


@pytest.mark.parametrize('colour, usable', [
    ('#e41a1c', '#e41a1c'), ('#FFF', '#FFF'), ('red', 'red'), ('r', '#ff0000'),
    (BAD_COLOUR, None), ('', None), (' ', None), (5, None), (None, None), (['#fff'], None),
    ({}, None), (True, None), (float('nan'), None),
])
def test_a_colour_is_usable_when_the_plot_and_the_widgets_can_draw_it(qapp, colour, usable):
    from gui import configuration

    assert configuration.usable_colour(colour) == usable


def test_every_value_made_wrong_normalises_to_values_of_the_right_kind(qapp, good):
    """The whole cross product, on the normaliser alone: fast."""
    from PySide6.QtGui import QColor
    from core.bus_types import decode_key
    from gui import configuration

    def check(config: dict) -> None:
        flags = ('step_lines', 'show_data_points', 'hide_plot_lines', 'multi_axis',
                 'stacked', 'multistack', 'cursor1', 'cursor2', 'name_show_channel',
                 'name_show_message', 'synchronize_tabs')
        assert all(isinstance(config[name], bool) for name in flags if name in config)
        for name in ('measurement_path', 'blf_path', 'dbc_path'):
            assert config.get(name) is None or isinstance(config[name], str)
        colours = [config.get('plot_background_color', '#000'),
                   *config.get('signal_colors', {}).values(),
                   *config.get('group_colors', {}).values()]
        entries = [entry for entry in config.get('signals', []) if not isinstance(entry, str)]
        for tab in config.get('tabs', []):
            assert isinstance(tab.get('name', ''), str)
            assert isinstance(tab.get('plot_type', ''), str)
            assert all(isinstance(tab[name], bool) for name in
                       ('show_data_points', 'hide_plot_lines', 'cursor1', 'cursor2')
                       if name in tab)
            entries += tab.get('signals', [])
        for entry in entries:
            assert isinstance(entry['key'], str) and entry['key']
            colours += [entry['color']] if 'color' in entry else []
            assert all(isinstance(entry[name], bool) for name in
                       ('visible', 'axis_visible', 'own_axis') if name in entry)
            assert type(entry.get('multistack_id', 0)) is int
            assert isinstance(entry.get('group', ''), str)
            assert isinstance(entry.get('line_style', ''), str)
        assert all(isinstance(colour, str) and QColor(colour).isValid() for colour in colours)
        assert all(type(width) is int and 0 <= width <= 10_000
                   for width in config.get('table_column_widths', []))
        assert type(config.get('current_tab', 0)) is int
        channels = config.get('channel_config', {}).get('channels', {})
        assert all(isinstance(path, str) and decode_key(key) for key, path in channels.items())
        assert isinstance(config.get('channel_config', {}).get('name', ''), str)
        for name in ('markers', 'generated_signals', 'tabs', 'signals'):
            assert isinstance(config.get(name, []), list)

    count = 0
    for label, mutated in _mutations(good):
        try:
            once = configuration.normalised(mutated)
            check(once)
            assert configuration.normalised(copy.deepcopy(once)) == once
        except Exception as error:
            raise AssertionError(f'{label}: {type(error).__name__}: {error}') from error
        count += 1
    assert count > 1000


@pytest.mark.parametrize('top', _TOP_KEYS)
def test_each_value_made_wrong_while_data_is_loaded(
        qapp, windows, good, uncaught, top):
    window = windows()
    _load(window, qapp, good)
    window._apply_configuration(copy.deepcopy(good), 'good.json')
    _wait_until_idle(window, qapp)
    baseline = _look(window)
    failures = []

    for label, mutated in _mutations(good, top, each=1):
        try:
            _clear(window)
            window._apply_configuration(mutated, 'mutated.json')
            _wait_until_idle(window, qapp)
            _assert_sound(window)
        except Exception as error:  # noqa: BLE001 - every failure is reported
            failures.append(f'{label}: {type(error).__name__}: {error}')
        failures += [f'{label}: uncaught {error}' for error in uncaught]
        uncaught.clear()
    assert not failures, '\n'.join(failures[:40])

    # Nothing of them sticks: the good configuration loads as it did.
    _clear(window)
    window._apply_configuration(copy.deepcopy(good), 'good.json')
    _wait_until_idle(window, qapp)
    assert not uncaught
    assert _look(window) == baseline


@pytest.mark.parametrize('top', _TOP_KEYS)
def test_each_value_made_wrong_in_the_last_session(
        qapp, monkeypatch, settings, windows, good, offered, uncaught, top):
    session = settings / session_restore.FILE_NAME
    window = windows(show=True)
    # The decode itself is not what is tested here.
    monkeypatch.setattr(window, 'load_data', Mock())
    failures = []

    for label, mutated in _mutations(good, top, each=2):
        try:
            session.write_text(json.dumps(mutated), encoding='utf-8')
            window.measurement_path = window.blf_path = None
            window._pending_tabs = None
            window._offer_last_session()
            qapp.processEvents()
            _assert_sound(window)
            # What is kept from it can be read back.
            window._keep_last_session(window._configuration_dict())
            assert session_restore.read(session) is not None
        except Exception as error:  # noqa: BLE001 - every failure is reported
            failures.append(f'{label}: {type(error).__name__}: {error}')
        failures += [f'{label}: uncaught {error}' for error in uncaught]
        uncaught.clear()

    assert not failures, '\n'.join(failures[:40])
