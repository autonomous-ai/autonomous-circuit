"""The case build waterfall ledger: stage order, two repair tries, notes, frozen parts."""
import json
from pathlib import Path
import tempfile
import unittest

from kicadpy import case_waterfall as wf


def put(root, name, text):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def summary(*entries):
    return {'checks': [{'name': name, 'stage': stage, 'kind': kind, 'level': level, 'message': message}
                       for name, stage, kind, level, message in entries]}


APPROVE = {'decision': 'approve', 'findings': []}
REVISE = {'decision': 'revise', 'findings': [{'path': 'case/frame.py', 'message': 'thin rib breaks'}]}


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_stages_come_in_build_order_whatever_the_file_says(self):
        put(self.root, 'case/checks.json', json.dumps({'stages': {
            'lid': {'parts': ['lid']}, 'joints': {'parts': ['pin']}, 'simple': {'parts': ['frame', 'tray']}},
            'checks': []}))
        stages, problems = wf.plan(self.root)
        self.assertEqual(list(stages), ['simple', 'joints', 'lid'])
        self.assertEqual(stages['simple'], ['frame', 'tray'])
        self.assertEqual(problems, [])

    def test_no_stages_means_no_waterfall_and_bad_stages_are_reported(self):
        self.assertEqual(wf.plan(self.root), (None, []))
        put(self.root, 'case/checks.json', json.dumps({'checks': []}))
        self.assertEqual(wf.plan(self.root), (None, []))
        put(self.root, 'case/checks.json', json.dumps({'stages': {'simple': {'parts': 'frame'}, 'covers': {}}}))
        stages, problems = wf.plan(self.root)
        self.assertEqual(stages, {'simple': []})
        self.assertEqual(len(problems), 2)
        self.assertIn("unknown stage 'covers'", problems[1])


class LedgerTests(unittest.TestCase):
    def ledger(self):
        return wf.start({'simple': ['frame'], 'moving': ['leg'], 'joints': ['pin']}, now=0)

    def test_a_stage_passes_on_its_checks_and_the_tester_then_the_next_is_current(self):
        ledger = self.ledger()
        self.assertEqual(wf.current(ledger), 'simple')
        keys = wf.stage_keys(summary(('print', 'simple', 'check', 'pass', 'ok'),
                                     ('sweep', 'moving', 'motion', 'fail', 'hits')), 'simple')
        self.assertEqual(keys, {'check': ('pass', 'print: ok')})
        self.assertEqual(wf.machine_open(ledger, 'simple', keys), [])
        self.assertEqual(wf.advance(ledger, 'simple', keys, APPROVE, now=10), 'passed')
        self.assertEqual(wf.current(ledger), 'moving')
        self.assertFalse(wf.done(ledger))

    def test_two_repair_tries_then_a_note_and_the_team_moves_on(self):
        ledger = self.ledger()
        failing = wf.stage_keys(summary(('print', 'simple', 'check', 'fail', 'overhang 70 deg')), 'simple')
        for now in (1, 2):  # the build and the first repair
            self.assertEqual(wf.advance(ledger, 'simple', failing, None, now=now), 'open')
        self.assertEqual(wf.machine_open(ledger, 'simple', failing), ['check'])
        self.assertEqual(wf.advance(ledger, 'simple', failing, None, now=3), 'open')  # the second repair failed
        self.assertEqual(wf.machine_open(ledger, 'simple', failing), [])  # noted: the tester may look now
        self.assertEqual(wf.advance(ledger, 'simple', failing, APPROVE, now=4), 'noted')
        self.assertEqual(ledger['notes'][0]['stage'], 'simple')
        self.assertIn('still failing after 2 repair tries: print: overhang 70 deg', ledger['notes'][0]['message'])
        self.assertEqual(len(ledger['notes']), 1)  # a noted check is not counted again
        self.assertEqual(wf.current(ledger), 'moving')

    def test_print_and_motion_of_moving_parts_have_their_own_tries(self):
        ledger = self.ledger()
        ledger['stages']['simple']['status'] = 'passed'
        keys = wf.stage_keys(summary(('legs-print', 'moving', 'print', 'fail', 'bridge 40 mm'),
                                     ('legs-sweep', 'moving', 'motion', 'pass', 'clear')), 'moving')
        wf.advance(ledger, 'moving', keys, None, now=1)
        keys = wf.stage_keys(summary(('legs-print', 'moving', 'print', 'pass', 'ok'),
                                     ('legs-sweep', 'moving', 'motion', 'fail', 'jams at 80 deg')), 'moving')
        wf.advance(ledger, 'moving', keys, None, now=2)
        self.assertEqual(ledger['stages']['moving']['fails'], {'print': 1, 'motion': 1})
        self.assertEqual(ledger['notes'], [])

    def test_the_tester_review_is_a_budget_of_its_own(self):
        ledger = self.ledger()
        keys = wf.stage_keys(summary(('print', 'simple', 'check', 'pass', 'ok')), 'simple')
        for now in (1, 2, 3):
            self.assertEqual(wf.advance(ledger, 'simple', keys, REVISE, now=now), 'open' if now < 3 else 'noted')
        self.assertEqual(ledger['notes'][0]['key'], 'review')
        self.assertIn('thin rib breaks', ledger['notes'][0]['message'])

    def test_a_stage_with_no_check_is_unverified_not_a_pass(self):
        keys = wf.stage_keys(summary(('print', 'simple', 'check', 'pass', 'ok')), 'joints')
        self.assertEqual(keys['check'][0], 'unverified')
        self.assertIn('declare one', keys['check'][1])

    def test_over_the_stage_time_cap_notes_what_fails_and_moves_on(self):
        ledger = self.ledger()
        failing = wf.stage_keys(summary(('print', 'simple', 'check', 'fail', 'warps')), 'simple')
        wf.advance(ledger, 'simple', failing, None, now=0)
        status = wf.advance(ledger, 'simple', failing, None, now=wf.STAGE_SECONDS['simple'] + 1)
        self.assertEqual(status, 'open')  # the tester still gets its look at what was built
        self.assertIn('stage cap', ledger['notes'][0]['message'])
        self.assertEqual(wf.advance(ledger, 'simple', failing, APPROVE, now=wf.STAGE_SECONDS['simple'] + 2), 'noted')

    def test_close_all_and_the_end(self):
        ledger = self.ledger()
        wf.close_all(ledger, 'out of time', now=5)
        self.assertTrue(wf.done(ledger))
        self.assertEqual(len(ledger['notes']), 3)

    def test_update_plan_keeps_closed_stages(self):
        ledger = self.ledger()
        ledger['stages']['simple']['status'] = 'passed'
        wf.update_plan(ledger, {'simple': ['frame', 'new'], 'moving': ['leg', 'arm'], 'lid': ['lid']})
        self.assertEqual(ledger['parts']['simple'], ['frame'])
        self.assertEqual(ledger['parts']['moving'], ['leg', 'arm'])
        self.assertEqual(ledger['plan'], ['simple', 'moving', 'joints', 'lid'])


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_part_files_and_a_step_digest_without_its_timestamp(self):
        put(self.root, 'case/out/parts.json', json.dumps({'parts': [
            {'name': 'frame', 'stl': 'frame.stl', 'step': 'frame.step'},
            {'name': 'leg', 'stl': 'leg.stl'}, {'name': 'evil', 'stl': '../x.stl'}]}))
        put(self.root, 'case/out/frame.stl', 'solid frame')
        put(self.root, 'case/out/leg.stl', 'solid leg')
        step = ("ISO-10303-21;\nHEADER;\nFILE_NAME('frame','{}');\nENDSEC;\nDATA;\n"
                "#7 = PRODUCT('Open CASCADE STEP translator 7.8 {}');\n#1=POINT();\nENDSEC;\n")
        path = put(self.root, 'case/out/frame.step', step.format('2026-11-12T10:00:00', 1))
        self.assertEqual(wf.part_files(self.root, ['frame', 'evil']), ['case/out/frame.step', 'case/out/frame.stl'])
        first = wf.digest(path)
        put(self.root, 'case/out/frame.step', step.format('2026-11-12T11:00:00', 2))  # a later export
        self.assertEqual(wf.digest(path), first)
        put(self.root, 'case/out/frame.step', step.format('x', 1).replace('POINT()', 'POINT(1)'))
        self.assertNotEqual(wf.digest(path), first)

    def test_notes_list_and_builder_prompt(self):
        ledger = wf.start({'simple': ['frame'], 'moving': ['leg']}, now=0)
        self.assertIn('No notes: every stage passed', wf.notes_markdown(ledger))
        text = wf.prompt_text(ledger)
        self.assertIn('Current stage: simple', text)
        self.assertIn('Build or repair ONLY this stage', text)
        ledger['stages']['simple']['status'] = 'noted'
        ledger['frozen']['case/out/frame.stl'] = {'stage': 'simple'}
        wf.note(ledger, 'simple', 'check', 'warps at the corner', now=1)
        ledger['end'], ledger['endSummary'] = 'waive', 'acceptable for V0'
        notes = wf.notes_markdown(ledger)
        self.assertIn('- [ ] **Simple parts (frame/box and small parts)** (check): warps at the corner', notes)
        self.assertIn('- Simple parts (frame/box and small parts): moved on with notes', notes)
        self.assertIn('Lead review at the end: waive — acceptable for V0', notes)
        text = wf.prompt_text(ledger)
        self.assertIn('Current stage: moving', text)
        self.assertIn('never rework their parts', text)
        self.assertIn('case/out/frame.stl', text)
        self.assertEqual(wf.brief(ledger)['notes'], ['simple/check: warps at the corner'])



class ReuseAndStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = wf.start({'simple': ['frame', 'tray'], 'moving': ['leg']}, now=0)

    def test_a_closed_stage_or_frozen_parts_reuse_their_recorded_checks(self):
        wf.record_checks(self.ledger, 'simple', summary(('print', 'simple', 'check', 'pass', 'ok'),
                                                        ('sweep', 'moving', 'motion', 'fail', 'hits')))
        self.assertEqual(self.ledger['checkLevels'], {'print': ['pass', 'ok']})
        checks = [{'name': 'print', 'stage': 'simple', 'parts': []},
                  {'name': 'tray-fit', 'stage': 'moving', 'parts': ['tray']},
                  {'name': 'new', 'stage': 'simple', 'parts': []}]
        self.assertEqual(wf.reuse_for(self.ledger, checks), {})  # the stage is still open
        self.ledger['stages']['simple']['status'] = 'passed'
        self.assertEqual(wf.reuse_for(self.ledger, checks), {'print': ('pass', 'ok')})
        self.ledger['checkLevels']['tray-fit'] = ['review', 'glue face 4 mm']
        self.ledger['frozen']['case/out/tray.stl'] = {'part': 'tray', 'stage': 'simple'}
        self.assertEqual(wf.reuse_for(self.ledger, checks)['tray-fit'], ('review', 'glue face 4 mm'))

    def test_a_follow_up_keeps_what_the_earlier_run_closed(self):
        earlier = {'finished': True, 'parts': {'frame': {'status': 'done'}, 'tray': {'status': 'note'},
                                               'leg': {'status': 'working'}},
                   'checks': {'print': ['pass', 'ok'], 'bad': ['great', 'x']}}
        self.assertEqual(wf.previous_parts(earlier), {'frame', 'tray'})
        self.assertEqual(wf.previous_parts({**earlier, 'finished': False}), set())
        wf.seed(self.ledger, earlier, {'frame', 'tray'})
        self.assertEqual(self.ledger['stages']['simple']['status'], 'passed')
        self.assertEqual(self.ledger['stages']['moving']['status'], 'open')
        self.assertEqual(self.ledger['checkLevels'], {'print': ['pass', 'ok']})
        self.assertIn('This is a follow-up request', wf.prompt_text(self.ledger))
        self.assertTrue(wf.brief(self.ledger)['followUp'])
        put(self.root, 'case/checks.json', json.dumps({'rework': ['tray', 3], 'stages': {
            'simple': {'parts': ['frame'], 'glueOn': ['tray']}}}))
        self.assertEqual(wf.rework(self.root), ['tray'])
        self.assertEqual(wf.glue_on(self.root), ['tray'])

    def test_status_document_gives_every_part_its_chip(self):
        doc = wf.status_document(self.ledger, 'testing', glue=['tray'])
        self.assertEqual([(item['name'], item['status']) for item in doc['stages']],
                         [('simple', 'testing'), ('moving', 'waiting')])
        self.assertEqual(doc['parts']['tray'], {'stage': 'simple', 'group': 'glue-on', 'status': 'testing'})
        self.assertEqual(doc['parts']['frame']['group'], 'frame')
        self.assertEqual(wf.status_document(self.ledger)['stages'][0]['status'], 'working')
        self.ledger['stages']['simple']['status'] = 'passed'
        self.ledger['stages']['moving']['status'] = 'noted'
        wf.note(self.ledger, 'moving', 'motion', 'jams at 80 deg', now=1)
        doc = wf.status_document(self.ledger)
        self.assertEqual(doc['parts']['frame']['status'], 'valid')
        self.assertEqual(doc['parts']['leg']['status'], 'note')
        self.assertEqual(doc['stages'][1]['notes'], ['jams at 80 deg'])
        self.ledger['finished'] = True
        self.assertEqual(wf.status_document(self.ledger)['parts']['frame']['status'], 'done')
        self.assertTrue(set(wf.STATUSES) >= {item['status'] for item in doc['stages']})

if __name__ == '__main__':
    unittest.main()
