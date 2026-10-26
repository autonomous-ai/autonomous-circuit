"""Lead advice must remain bound to one contract and cite actionable files."""
import unittest

from kicadpy.parallel_contract import ADVICE_SCHEMA, validate


def advice(decision='repair'):
    return {
        'contractSha256': 'sealed-contract-hash',
        'stage': 'board',
        'decision': decision,
        'summary': 'Repair the existing board against the sealed contract.',
        'actions': [{'path': 'design/main.kicad_pcb', 'message': 'Fix the unconnected pad.'}],
        'changedFields': [],
    }


class AdviceSchemaTests(unittest.TestCase):
    def test_accepts_each_decision_with_all_required_fields(self):
        for decision in ('repair', 'interface_change', 'external_blocker'):
            with self.subTest(decision=decision):
                validate(advice(decision), ADVICE_SCHEMA)

    def test_rejects_missing_or_extra_fields(self):
        for key in advice():
            malformed = advice()
            del malformed[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate(malformed, ADVICE_SCHEMA)
        malformed = advice()
        malformed['unreviewed'] = True
        with self.assertRaises(ValueError):
            validate(malformed, ADVICE_SCHEMA)

    def test_rejects_unknown_decision_and_uncited_actions(self):
        malformed = advice('approve')
        with self.assertRaises(ValueError):
            validate(malformed, ADVICE_SCHEMA)
        for action in ({'message': 'Fix the board.'},
                       {'path': 'design/main.kicad_pcb'},
                       {'path': 'design/main.kicad_pcb', 'message': 'Fix the board.', 'extra': 'field'},
                       {'path': ' ', 'message': 'Fix the board.'}):
            malformed = advice()
            malformed['actions'] = [action]
            with self.subTest(action=action), self.assertRaises(ValueError):
                validate(malformed, ADVICE_SCHEMA)

    def test_changed_fields_must_be_a_list_of_nonempty_strings(self):
        for fields in ('board.outlineMm', [None], [' ']):
            malformed = advice('interface_change')
            malformed['changedFields'] = fields
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate(malformed, ADVICE_SCHEMA)
        revised = advice('interface_change')
        revised['changedFields'] = ['board.outlineMm', 'pins']
        validate(revised, ADVICE_SCHEMA)


if __name__ == '__main__':
    unittest.main()
