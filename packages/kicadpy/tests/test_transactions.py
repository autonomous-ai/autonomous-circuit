import json
from pathlib import Path
import shutil
import pytest
from kicadpy import checks, toolchain
from kicadpy.engine import Engine
from kicadpy.project import manifest, revision


@pytest.fixture
def engine(tmp_path):
    try:
        toolchain.versions()
        probe = tmp_path / 'probe'
        shutil.copytree(Path(__file__).parent / 'fixtures/tiny', probe)
        toolchain.worker('inspect', probe / 'tiny.kicad_pcb')
    except (RuntimeError, OSError) as exc:
        pytest.skip(str(exc))
    root = tmp_path / 'design'
    shutil.copytree(Path(__file__).parent / 'fixtures/tiny', root)
    return Engine(root / 'tiny.kicad_pro')


def request(engine, repair=True):
    state = engine.inspect()
    track = next(t for t in state['copper'] if t['net'] == '/SIGNAL')
    scope = {'uuids': [track['uuid']], 'nets': ['/SIGNAL'], 'regionMm': [0, 0, 30, 20]}
    points = [track['start'], [5, 4], [25, 4], track['end']] if repair else [track['start'], track['end']]
    return state['revision'], scope, [{'op': 'replace_track', 'uuid': track['uuid'], 'points': points}]


def test_short_is_detected_and_refused(engine):
    rev, scope, edits = request(engine, False)
    candidate = engine.apply(rev, scope, edits)['candidate']
    report = engine.check(candidate)
    assert not report['passed']
    assert any(f['type'] in ('shorting_items', 'clearance') for f in report['findings'])
    with pytest.raises(ValueError, match='passing check'):
        engine.commit(candidate, rev)
    assert revision(engine.project.root) == rev


def test_three_repair_check_commit_undo_cycles(engine):
    original = manifest(engine.project.root)
    for _ in range(3):
        rev, scope, edits = request(engine)
        candidate = engine.apply(rev, scope, edits)['candidate']
        assert manifest(engine.project.root) == original
        report = engine.check(candidate)
        assert report['passed'], report['findings']
        assert not report['diff']['dependenciesChanged']
        assert not report['diff']['settingsChanged']
        assert all(x['kind'] == 'segment' for x in report['diff']['objects'])
        committed = engine.commit(candidate, rev)
        assert not committed['fabricationReady']
        engine.undo(rev, committed['revision'])
        assert manifest(engine.project.root) == original


def test_stale_and_out_of_scope_edits_do_not_write(engine):
    rev, scope, edits = request(engine)
    with pytest.raises(ValueError, match='stale revision'):
        engine.apply('0' * 64, scope, edits)
    scope['regionMm'] = [0, 0, 2, 2]
    with pytest.raises(RuntimeError, match='out-of-scope'):
        engine.apply(rev, scope, edits)
    assert revision(engine.project.root) == rev


def test_tampered_candidate_cannot_be_checked_or_committed(engine):
    rev, scope, edits = request(engine)
    candidate = engine.apply(rev, scope, edits)['candidate']
    path, _ = engine._candidate(candidate)
    (path / 'design/tiny.kicad_sch').write_text('changed')
    with pytest.raises(ValueError, match='integrity'):
        engine.check(candidate)
    with pytest.raises(FileNotFoundError):
        engine.commit(candidate, rev)
    assert revision(engine.project.root) == rev


def test_report_missing_sections_is_not_a_pass():
    with pytest.raises(ValueError, match='incomplete'):
        checks.parse_reports({}, {})
    drc = dict(violations=[], unconnected_items=[], schematic_parity=[], ignored_checks=[])
    with pytest.raises(ValueError, match='incomplete'):
        checks.parse_reports(drc, {'sheets': [], 'ignored_checks': []})


def test_snapshot_dependency_edit_invalidates_revision(engine):
    rev = engine.snapshot()['revision']
    (engine.project.root / 'product.json').write_text('{"layers":4}')
    assert revision(engine.project.root) != rev
    with pytest.raises(ValueError, match='dependencies'):
        engine.undo(rev, revision(engine.project.root))


def test_worker_failure_cannot_mutate_live_files(engine, monkeypatch):
    rev, scope, edits = request(engine)
    monkeypatch.setattr(toolchain, 'worker', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError, match='crash'):
        engine.apply(rev, scope, edits)
    assert revision(engine.project.root) == rev


def test_checked_candidate_detects_stale_live_source_and_report(engine):
    rev, scope, edits = request(engine)
    candidate = engine.apply(rev, scope, edits)['candidate']
    assert engine.check(candidate)['passed']
    source = engine.project.pcb.read_bytes()
    engine.project.pcb.write_bytes(source + b'\n')
    with pytest.raises(ValueError, match='stale revision'):
        engine.commit(candidate, rev)
    engine.project.pcb.write_bytes(source)
    path, _ = engine._candidate(candidate)
    (path / 'reports/check.json').write_text('{}')
    with pytest.raises(ValueError, match='passing check'):
        engine.commit(candidate, rev)
    assert revision(engine.project.root) == rev


def test_snapshot_tampering_is_detected(engine):
    rev = engine.snapshot()['revision']
    (engine.project.store / 'snapshots' / rev / 'tiny.kicad_sch').write_text('tampered')
    with pytest.raises(ValueError, match='integrity'):
        engine.undo(rev, rev)


def test_parent_requirements_are_in_snapshot(tmp_path):
    root = tmp_path / 'device'
    shutil.copytree(Path(__file__).parent / 'fixtures/tiny', root / 'design')
    (root / 'product.json').write_text('{"layers":2}')
    engine = Engine(root / 'design/tiny.kicad_pro')
    rev = engine.snapshot()['revision']
    snapshot = engine.project.store / 'snapshots' / rev
    assert (snapshot / 'product.json').is_file()
    assert (snapshot / 'design/tiny.kicad_sch').is_file()
    (root / 'product.json').write_text('{"layers":4}')
    with pytest.raises(ValueError, match='stale'):
        engine.project.expect(rev)


def test_native_ses_preserves_omitted_protected_copper(engine, monkeypatch):
    from types import SimpleNamespace
    from circuitpy import toolchain as shared
    rev, scope, _ = request(engine)
    before = engine.inspect()
    protected = next(t for t in before['copper'] if t['net'] == '/OTHER')
    def route(dsn, ses, **kwargs):
        assert '(net /OTHER)(type fix)' in dsn.read_text()
        shutil.copyfile(Path(__file__).parent / 'fixtures/native-route.ses', ses)
        return SimpleNamespace(output='captured Freerouting 2.4.1 response')
    monkeypatch.setattr(shared, 'run_freerouting', route)
    candidate = engine.route(rev, scope)['candidate']
    report = engine.check(candidate)
    assert report['passed'], report['findings']
    assert protected['uuid'] not in [x['uuid'] for x in report['diff']['objects']]
    result = engine.commit(candidate, rev)
    assert protected in engine.inspect()['copper']
    engine.undo(rev, result['revision'])


def test_external_library_dependency_is_refused(engine):
    (engine.project.root / 'fp-lib-table').write_text('(fp_lib_table (lib (name "External") (uri "/outside/External.pretty")))')
    with pytest.raises(ValueError, match='external project dependency'):
        engine.snapshot()


def test_zone_fill_changes_require_explicit_scope():
    delta = {'settingsChanged': False, 'dependenciesChanged': [], 'objects': [], 'zoneFillsChanged': ['zone-1']}
    with pytest.raises(ValueError, match='zone fill'):
        Engine._guard(delta, {'allowed': [], 'scope': {}})
    Engine._guard(delta, {'allowed': [], 'scope': {'refillZones': ['zone-1']}})


def test_worker_response_survives_a_teardown_crash(monkeypatch):
    # pcbnew's SWIG objects segfault in Py_FinalizeEx after the work is done
    # (KiCad 10.0.5, 2026-09-16: 17 crash reports in eight minutes, every one
    # after the response line). The line is the result; a crash without it is
    # still the error, with stderr as the reason.
    from types import SimpleNamespace
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=-11, stdout='wx noise\nKICADPY: {"ok": 1}\n', stderr='assert "traits" failed')
    monkeypatch.setattr(toolchain.subprocess, 'run', run)
    monkeypatch.setattr(toolchain, 'executable', lambda kind: '/usr/bin/true')
    assert toolchain.worker('inspect', 'x.kicad_pcb') == {'ok': 1}
    monkeypatch.setattr(toolchain.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=-11, stdout='', stderr='assert "traits" failed'))
    with pytest.raises(RuntimeError, match='traits'):
        toolchain.worker('inspect', 'x.kicad_pcb')


def test_scratch_copies_outside_design_are_not_inputs(tmp_path):
    # An agent keeps candidate boards under build/ and tools/; they must not
    # change the revision or the design inputs (Board mới, 2026-09-17).
    root = tmp_path / 'device'
    shutil.copytree(Path(__file__).parent / 'fixtures/tiny', root / 'design')
    (root / 'product.json').write_text('{"layers":2}')
    before = revision(root)
    (root / 'build' / 'cand' / 'design').mkdir(parents=True)
    shutil.copyfile(root / 'design/tiny.kicad_pcb', root / 'build/cand/design/tiny.kicad_pcb')
    (root / 'tools').mkdir(); (root / 'tools/scratch.kicad_pcb').write_text('(kicad_pcb)')
    assert revision(root) == before
    assert not any(k.startswith(('build/', 'tools/')) for k in manifest(root))
    (root / 'engineering').mkdir(); (root / 'engineering/power.md').write_text('evidence')
    assert revision(root) != before and 'engineering/power.md' in manifest(root)
