# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""
The last session: closing with a configuration that differs from the one last
saved or opened asks Save / Don't save / Cancel; unless the answer is Cancel
the session is kept in osvanta_last_session.json, rewritten once a minute
while it changes, and every start offers to reopen it. Nothing is asked or
kept while a load or calculation runs, under the release test, or with
OSVANTA_SESSION_RESTORE=0.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from gui import release_test, session_restore
from gui.session_restore import CloseAnswer, OfferAnswer


def _csv(path: Path, signals: int = 4, rows: int = 50) -> Path:
    header = 'Timestamps [s],' + ','.join(f'Sig{i} [V]' for i in range(signals))
    lines = [header] + [
        f'{row * 0.01:.3f},' + ','.join(str(row + i) for i in range(signals))
        for row in range(rows)
    ]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def _key(index: int) -> str:
    """The key the CSV reader gives column *index* of _csv()."""
    return f'CH?::CSV::Sig{index}'


# ── The module ───────────────────────────────────────────────────────────


@pytest.mark.parametrize('variable, argv, expected', [
    (None, ['app.py'], True),
    ('1', ['app.py'], True),
    ('0', ['app.py'], False),
    (None, ['app.py', release_test.SCENARIO_FLAG, 'x.json'], False),
])
def test_switched_off_by_the_variable_and_in_release_tests(monkeypatch, variable, argv, expected):
    if variable is None:
        monkeypatch.delenv(session_restore.DISABLE_VARIABLE, raising=False)
    else:
        monkeypatch.setenv(session_restore.DISABLE_VARIABLE, variable)

    assert session_restore.enabled(argv) is expected


def test_with_nothing_saved_or_opened_any_session_differs():
    assert session_restore.differs({'stacked': True}, None)


def test_a_change_differs_and_the_version_does_not():
    snapshot = {'version': '00.01.00', 'stacked': True, 'signals': [{'key': 'a'}]}

    assert not session_restore.differs(
        {'version': '00.02.03', 'stacked': True, 'signals': [{'key': 'a'}]}, snapshot)
    assert session_restore.differs(
        {'version': '00.01.00', 'stacked': False, 'signals': [{'key': 'a'}]}, snapshot)
    assert session_restore.differs(
        {'version': '00.01.00', 'stacked': True, 'signals': []}, snapshot)


def test_keys_an_older_file_lacks_do_not_count():
    snapshot = {'stacked': True}

    assert not session_restore.differs({'stacked': True, 'markers': []}, snapshot)


def test_compared_as_a_file_holds_it():
    # Tuples come back from JSON as lists: the same configuration.
    assert not session_restore.differs({'widths': (10, 20)}, {'widths': [10, 20]})


@pytest.mark.parametrize('config, expected', [
    ({}, False),
    ({'measurement_path': None, 'signals': [], 'generated_signals': []}, False),
    ({'measurement_path': 'a.mf4'}, True),
    ({'signals': [{'key': 'a'}]}, True),
    ({'generated_signals': [{'name': 'x'}]}, True),
])
def test_only_a_session_that_holds_something_is_kept(config, expected):
    assert session_restore.holds_something(config) is expected


def test_written_and_read_back_with_where_it_was_saved(tmp_path):
    path = tmp_path / session_restore.FILE_NAME
    config = {'measurement_path': 'C:/logs/run.mf4', 'signals': [{'key': 'a'}]}

    session_restore.write(path, config, 'C:/configs/run.json')
    data = session_restore.read(path)

    assert data['signals'] == [{'key': 'a'}]
    assert session_restore.saved_to(data) == 'C:/configs/run.json'
    session_restore.write(path, config, None)
    assert session_restore.saved_to(session_restore.read(path)) is None
    assert 'last_session' not in config


def test_no_file_is_no_session(tmp_path):
    assert session_restore.read(tmp_path / session_restore.FILE_NAME) is None


@pytest.mark.parametrize('content', ['{"signals": [', '[1, 2]', ''])
def test_a_damaged_file_raises(tmp_path, content):
    path = tmp_path / session_restore.FILE_NAME
    path.write_text(content, encoding='utf-8')

    with pytest.raises(ValueError):
        session_restore.read(path)


def test_a_write_cut_short_leaves_the_previous_session_whole(tmp_path, monkeypatch):
    path = tmp_path / session_restore.FILE_NAME
    session_restore.write(path, {'signals': [{'key': 'kept'}]}, None)
    real_write_text = Path.write_text

    def cut_short(self, text, *args, **kwargs):
        real_write_text(self, text[:10], *args, **kwargs)
        raise OSError('disk full')

    monkeypatch.setattr(Path, 'write_text', cut_short)
    with pytest.raises(OSError):
        session_restore.write(path, {'signals': [{'key': 'new'}]}, None)
    monkeypatch.undo()

    assert session_restore.read(path)['signals'] == [{'key': 'kept'}]


def test_the_summary_names_the_measurement_the_signals_and_the_save():
    data = {'measurement_path': 'C:/logs/run.mf4', 'signals': [{'key': 'a'}, {'key': 'b'}]}

    assert session_restore.summary(data) == (
        'Measurement: run.mf4\nPlotted signals: 2\nNot saved')
    data['last_session'] = {'saved_to': 'C:/configs/run.json'}
    assert session_restore.summary(data).endswith('Saved as: run.json')
    assert session_restore.summary({}) == 'Measurement: none\nPlotted signals: 0\nNot saved'


@pytest.mark.parametrize('button, answer', [
    ('Save', CloseAnswer.SAVE),
    ('Discard', CloseAnswer.DISCARD),
    ('Cancel', CloseAnswer.CANCEL),
    (None, CloseAnswer.CANCEL),
])
def test_each_close_button_gives_its_answer(qapp, monkeypatch, button, answer):
    from PySide6.QtWidgets import QMessageBox

    seen = []

    def exec_(box):
        seen.append(box.button(QMessageBox.StandardButton.Discard).text())
        if button:
            box.button(getattr(QMessageBox.StandardButton, button)).click()
        return 0

    monkeypatch.setattr(QMessageBox, 'exec', exec_)

    assert session_restore.ask_to_save(None) is answer
    assert seen == ["Don't save"]


@pytest.mark.parametrize('button, answer', [
    ('Reopen', OfferAnswer.REOPEN),
    ('Not now', OfferAnswer.LATER),
    (None, OfferAnswer.LATER),
])
def test_each_offer_button_gives_its_answer(qapp, monkeypatch, button, answer):
    from PySide6.QtWidgets import QMessageBox

    shown = []

    def exec_(box):
        shown.append(box.informativeText())
        if button:
            next(b for b in box.buttons() if b.text() == button).click()
        return 0

    monkeypatch.setattr(QMessageBox, 'exec', exec_)

    assert session_restore.ask_to_reopen(None, 'Measurement: run.mf4') is answer
    assert shown[0].startswith('Measurement: run.mf4')


# ── In the main window ───────────────────────────────────────────────────


@pytest.fixture()
def session(tmp_path, monkeypatch):
    """The last session's path, in a settings folder of the test's own."""
    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    monkeypatch.setenv(session_restore.DISABLE_VARIABLE, '1')
    monkeypatch.setattr('sys.argv', ['app.py'])
    return tmp_path / session_restore.FILE_NAME


@pytest.fixture()
def asked(monkeypatch):
    """Records each close question; the user answers Don't save."""
    ask = Mock(return_value=CloseAnswer.DISCARD)
    monkeypatch.setattr(session_restore, 'ask_to_save', ask)
    return ask


@pytest.fixture()
def offered(monkeypatch):
    """Records each offer at start; the user answers Reopen."""
    offer = Mock(return_value=OfferAnswer.REOPEN)
    monkeypatch.setattr(session_restore, 'ask_to_reopen', offer)
    return offer


@pytest.fixture()
def boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    mocks = {}
    for name in ('information', 'warning', 'critical'):
        mocks[name] = Mock(return_value=QMessageBox.StandardButton.Ok)
        monkeypatch.setattr(QMessageBox, name, mocks[name])
    return mocks


@pytest.fixture()
def saved_as(tmp_path, monkeypatch):
    """The file the Save dialog answers with; None cancels it."""
    from PySide6.QtWidgets import QFileDialog

    answer = {'path': tmp_path / 'chosen.json', 'start': None}

    def get_save_file_name(parent, caption, start, *args, **kwargs):
        answer['start'] = start
        return (str(answer['path']), '') if answer['path'] else ('', '')

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', get_save_file_name)
    return answer


@pytest.fixture()
def windows(qapp):
    made = []

    def make(show: bool = False):
        from gui.main_window import MainWindow

        w = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
        w.resize(1200, 700)
        if show:
            w.show()
            qapp.processEvents()
        made.append(w)
        return w

    yield make
    for w in made:
        _wait_until_idle(w, qapp)
        w._session_restore = False
        w.close()
    qapp.processEvents()


def _wait_until_idle(window, qapp) -> None:
    deadline = time.monotonic() + 30.0
    while ((window._thread is not None or window._calc_thread is not None)
           and time.monotonic() < deadline):
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert window._thread is None and window._calc_thread is None, 'Work did not finish'


def _load(window, qapp, measurement: Path) -> list[str]:
    window.measurement_path = str(measurement)
    window.blf_path = str(measurement)
    window.load_data()
    _wait_until_idle(window, qapp)
    return sorted(window.store.all_keys())


def _plotted(window, qapp, tmp_path, count: int = 2) -> list[str]:
    keys = _load(window, qapp, _csv(tmp_path / 'run.csv'))[:count]
    window.add_signals_to_plot(keys)
    qapp.processEvents()
    return keys


def _wait_for(condition, qapp, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_an_empty_session_closes_without_asking_or_keeping(qapp, session, asked, windows):
    window = windows()

    assert window.close()

    asked.assert_not_called()
    assert not session.exists()


def test_a_changed_session_asks_and_dont_save_keeps_it_unsaved(
        qapp, tmp_path, session, asked, boxes, saved_as, windows):
    window = windows()
    keys = _plotted(window, qapp, tmp_path)

    assert window.close()

    asked.assert_called_once()
    data = session_restore.read(session)
    assert [signal['key'] for signal in data['signals']] == keys
    assert session_restore.saved_to(data) is None
    assert not saved_as['path'].exists()


def test_save_writes_the_chosen_file_and_keeps_the_session_as_saved(
        qapp, tmp_path, session, asked, boxes, saved_as, windows):
    asked.return_value = CloseAnswer.SAVE
    window = windows()
    keys = _plotted(window, qapp, tmp_path)

    assert window.close()

    chosen = json.loads(saved_as['path'].read_text(encoding='utf-8'))
    assert [signal['key'] for signal in chosen['signals']] == keys
    assert session_restore.saved_to(session_restore.read(session)) == str(saved_as['path'])


@pytest.mark.parametrize('answer, dialog', [
    (CloseAnswer.CANCEL, 'chosen.json'),
    (CloseAnswer.SAVE, None),
])
def test_cancel_or_a_cancelled_save_keeps_the_window_open(
        qapp, tmp_path, session, asked, boxes, saved_as, windows, answer, dialog):
    asked.return_value = answer
    saved_as['path'] = tmp_path / dialog if dialog else None
    window = windows(show=True)
    _plotted(window, qapp, tmp_path)

    assert not window.close()

    assert window.isVisible()
    assert window.store is not None
    assert not session.exists()
    # Asked again on the next close: the answer was not to close.
    asked.return_value = CloseAnswer.DISCARD
    assert window.close()
    assert asked.call_count == 2


def test_a_session_unchanged_since_its_save_closes_without_asking(
        qapp, tmp_path, session, asked, boxes, saved_as, windows):
    window = windows()
    _plotted(window, qapp, tmp_path)
    window.save_configuration()

    assert window.close()

    asked.assert_not_called()
    assert session_restore.saved_to(session_restore.read(session)) == str(saved_as['path'])


def test_the_save_dialog_starts_at_the_file_last_saved(
        qapp, tmp_path, session, boxes, saved_as, windows):
    window = windows()
    _plotted(window, qapp, tmp_path)

    window.save_configuration()
    first = saved_as['start']
    window.save_configuration()

    assert first == 'osvanta_config.json'
    assert saved_as['start'] == str(saved_as['path'])


def _open_configuration(window, qapp, path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, 'getOpenFileName',
                        lambda *args, **kwargs: (str(path), ''))
    window.load_configuration()
    _wait_until_idle(window, qapp)


def test_an_older_configuration_opened_and_closed_unchanged_does_not_ask(
        qapp, tmp_path, session, asked, boxes, saved_as, windows, monkeypatch):
    first = windows()
    _plotted(first, qapp, tmp_path, count=3)
    first.save_configuration()
    first._session_restore = False
    first.close()
    older = json.loads(saved_as['path'].read_text(encoding='utf-8'))
    older['version'] = '00.01.00'
    del older['markers']
    saved_as['path'].write_text(json.dumps(older), encoding='utf-8')

    window = windows(show=True)
    _open_configuration(window, qapp, saved_as['path'], monkeypatch)
    assert window.plot_panel.plotted_keys()

    assert window.close()
    asked.assert_not_called()


@pytest.mark.parametrize('change', ['remove', 'colour', 'marker', 'add'])
def test_a_change_after_opening_a_configuration_asks(
        qapp, tmp_path, session, asked, boxes, saved_as, windows, monkeypatch, change):
    first = windows()
    keys = _plotted(first, qapp, tmp_path, count=2)
    first.save_configuration()
    first._session_restore = False
    first.close()
    window = windows()
    _open_configuration(window, qapp, saved_as['path'], monkeypatch)

    if change == 'remove':
        window.plot_panel.remove_series(keys[0])
    elif change == 'colour':
        window.plot_panel.set_series_color(keys[0], '#123456')
    elif change == 'marker':
        window.plot_panel.add_marker(0.2)
    else:
        window.add_signals_to_plot([k for k in window.store.all_keys() if k not in keys][:1])
    qapp.processEvents()

    assert window.close()
    asked.assert_called_once()
    # Don't save: what is kept is no longer the file it was opened from.
    assert session_restore.saved_to(session_restore.read(session)) is None


def _kept_session(session: Path, measurement: Path, keys, saved_to=None) -> None:
    session_restore.write(session, {
        'measurement_path': str(measurement),
        'signals': [{'key': key} for key in keys],
    }, saved_to)


def test_a_start_offers_the_last_session_and_reopen_restores_it(
        qapp, tmp_path, session, asked, offered, boxes, windows):
    measurement = _csv(tmp_path / 'run.csv')
    _kept_session(session, measurement, [_key(1), _key(2)])

    window = windows(show=True)
    _wait_for(lambda: offered.called and window._thread is None, qapp)
    _wait_until_idle(window, qapp)

    offered.assert_called_once()
    assert 'Measurement: run.csv' in offered.call_args.args[1]
    assert window.plot_panel.plotted_keys() == [_key(1), _key(2)]
    # Never saved, so still unsaved: closing asks.
    assert window.close()
    asked.assert_called_once()


def test_a_reopened_session_that_was_saved_is_that_file_again(
        qapp, tmp_path, session, asked, offered, boxes, saved_as, windows):
    measurement = _csv(tmp_path / 'run.csv')
    first = windows()
    _load(first, qapp, measurement)
    first.add_signals_to_plot([_key(1)])
    first.save_configuration()
    first._session_restore = False
    first.close()
    data = json.loads(saved_as['path'].read_text(encoding='utf-8'))
    session_restore.write(session, data, str(saved_as['path']))

    window = windows(show=True)
    _wait_for(lambda: offered.called and window._thread is None, qapp)
    _wait_until_idle(window, qapp)

    assert window.plot_panel.plotted_keys() == [_key(1)]
    assert window.close()
    asked.assert_not_called()


def test_not_now_keeps_the_last_session_for_the_next_start(
        qapp, tmp_path, session, asked, offered, boxes, windows):
    offered.return_value = OfferAnswer.LATER
    _kept_session(session, _csv(tmp_path / 'run.csv'), [_key(1)])
    before = session.read_bytes()

    window = windows(show=True)
    _wait_for(lambda: offered.called, qapp)

    assert window.store is None
    # An empty window's refresh must not replace the session it declined.
    window._refresh_last_session()
    assert window.close()
    assert session.read_bytes() == before


def test_without_a_last_session_a_start_offers_nothing(qapp, session, offered, windows):
    window = windows(show=True)
    _wait_for(lambda: offered.called, qapp, seconds=1.5)

    offered.assert_not_called()
    assert window.isVisible()


def test_a_damaged_last_session_is_logged_and_not_offered(qapp, session, offered, windows):
    session.write_text('{"signals": [', encoding='utf-8')

    window = windows(show=True)
    _wait_for(lambda: 'Last session not offered' in window.log_box.toPlainText(), qapp)

    offered.assert_not_called()
    assert 'Last session not offered' in window.log_box.toPlainText()


def test_a_file_opened_before_the_offer_is_not_overridden(
        qapp, tmp_path, session, offered, boxes, monkeypatch, windows):
    monkeypatch.setattr(session_restore, 'OFFER_DELAY_MS', 300)
    _kept_session(session, _csv(tmp_path / 'other.csv'), [_key(1)])

    window = windows(show=True)
    window.measurement_path = str(_csv(tmp_path / 'run.csv'))
    window.blf_path = window.measurement_path
    _wait_for(lambda: False, qapp, seconds=0.6)

    offered.assert_not_called()


def test_a_last_session_whose_measurement_is_gone_warns_and_stays_whole(
        qapp, tmp_path, session, asked, offered, boxes, windows):
    _kept_session(session, tmp_path / 'gone.csv', [_key(1), _key(2)])
    before = session.read_bytes()

    window = windows(show=True)
    _wait_for(lambda: boxes['warning'].called, qapp)

    assert 'Measurement file not found' in boxes['warning'].call_args.args[1]
    # The layout waits for a decode that cannot come: neither the minute's
    # refresh nor the close may replace the session with half of it.
    window._refresh_last_session()
    assert window.close()
    asked.assert_not_called()
    assert session.read_bytes() == before


def test_the_refresh_writes_only_a_changed_session(
        qapp, tmp_path, session, boxes, windows, monkeypatch):
    real_write = session_restore.write
    write = Mock(side_effect=real_write)
    monkeypatch.setattr(session_restore, 'write', write)
    window = windows()
    keys = _plotted(window, qapp, tmp_path)

    window._refresh_last_session()
    window._refresh_last_session()
    assert write.call_count == 1
    window.plot_panel.set_series_color(keys[0], '#123456')
    window._refresh_last_session()

    assert write.call_count == 2
    assert session_restore.read(session)['signal_colors'][keys[0]] == '#123456'


def test_the_refresh_keeps_nothing_while_a_load_runs(
        qapp, tmp_path, session, boxes, windows):
    window = windows()
    _plotted(window, qapp, tmp_path)
    window.measurement_path = str(_csv(tmp_path / 'next.csv', signals=8, rows=20_000))
    window.blf_path = window.measurement_path

    window.load_data()
    assert window._thread is not None
    window._refresh_last_session()

    assert not session.exists()
    _wait_until_idle(window, qapp)


def test_a_close_during_a_load_neither_asks_nor_keeps(
        qapp, tmp_path, session, asked, boxes, windows):
    window = windows(show=True)
    _plotted(window, qapp, tmp_path)
    window.measurement_path = str(_csv(tmp_path / 'next.csv', signals=8, rows=20_000))
    window.blf_path = window.measurement_path

    window.load_data()
    window.close()
    _wait_for(lambda: window._thread is None, qapp, seconds=30)

    asked.assert_not_called()
    assert not session.exists()


def test_the_window_refreshes_on_its_timer(qapp, tmp_path, session, boxes, windows, monkeypatch):
    monkeypatch.setattr(session_restore, 'REFRESH_INTERVAL_MS', 20)
    window = windows(show=True)
    keys = _plotted(window, qapp, tmp_path)

    _wait_for(session.exists, qapp)

    assert [signal['key'] for signal in session_restore.read(session)['signals']] == keys


@pytest.mark.parametrize('switch', ['variable', 'release test'])
def test_switched_off_nothing_is_asked_or_kept(
        qapp, tmp_path, session, asked, offered, boxes, windows, monkeypatch, switch):
    if switch == 'variable':
        monkeypatch.setenv(session_restore.DISABLE_VARIABLE, '0')
    else:
        monkeypatch.setattr('sys.argv', ['app.py', release_test.SCENARIO_FLAG, 'x.json'])
    _kept_session(session, _csv(tmp_path / 'other.csv'), [_key(1)])
    before = session.read_bytes()
    monkeypatch.setattr(session_restore, 'REFRESH_INTERVAL_MS', 20)

    window = windows(show=True)
    _plotted(window, qapp, tmp_path)
    _wait_for(lambda: False, qapp, seconds=1.0)
    assert window.close()

    asked.assert_not_called()
    offered.assert_not_called()
    assert session.read_bytes() == before


# ── Signals added and removed while the session is being kept ────────────


def test_adding_and_removing_signals_under_a_constant_refresh_never_hangs(
        qapp, tmp_path, session, boxes, windows, monkeypatch):
    """The refresh fires on every pass of the event loop while signals come
    and go: each must finish quickly and leave a whole file. (The plot's own
    rebuild after each add or remove is what takes the time here, refresh or
    not; that is measured elsewhere.)"""
    from gui.main_window import MainWindow

    monkeypatch.setattr(session_restore, 'REFRESH_INTERVAL_MS', 0)
    real_refresh = MainWindow._refresh_last_session
    durations = []

    def timed_refresh(self):
        started = time.perf_counter()
        real_refresh(self)
        durations.append(time.perf_counter() - started)

    monkeypatch.setattr(MainWindow, '_refresh_last_session', timed_refresh)
    window = windows(show=True)
    keys = _load(window, qapp, _csv(tmp_path / 'run.csv', signals=24))

    for round_ in range(12):
        batch = keys[round_:round_ + 6]
        window.add_signals_to_plot(batch)
        qapp.processEvents()
        assert session_restore.read(session) is not None
        window.plot_panel.remove_series_many(batch[::2])
        qapp.processEvents()
        window.plot_panel.remove_series(batch[1])
        qapp.processEvents()
    _wait_for(lambda: False, qapp, seconds=0.2)

    assert len(durations) >= 36, f'the refresh ran {len(durations)} times'
    assert max(durations) < 0.25, f'slowest refresh {max(durations):.3f} s'
    kept = [signal['key'] for signal in session_restore.read(session)['signals']]
    assert kept == window.plot_panel.plotted_keys()
    assert not session.with_name(session.name + '.tmp').exists()


def test_signals_changed_while_the_save_dialog_is_open(
        qapp, tmp_path, session, asked, boxes, windows, monkeypatch):
    """A refresh that fires while Save's dialog is up, with the plot changing
    under it: the saved file is the session Save was chosen for, and both
    files stay whole."""
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(session_restore, 'REFRESH_INTERVAL_MS', 0)
    asked.return_value = CloseAnswer.SAVE
    chosen = tmp_path / 'chosen.json'
    window = windows(show=True)
    keys = _load(window, qapp, _csv(tmp_path / 'run.csv', signals=10))
    window.add_signals_to_plot(keys[:5])
    qapp.processEvents()

    def busy_dialog(*args, **kwargs):
        for key in keys[5:]:
            window.add_signals_to_plot([key])
            qapp.processEvents()
            window.plot_panel.remove_series(key)
            qapp.processEvents()
        return str(chosen), ''

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', busy_dialog)

    assert window.close()

    saved = json.loads(chosen.read_text(encoding='utf-8'))
    assert [signal['key'] for signal in saved['signals']] == keys[:5]
    assert session_restore.read(session) is not None
