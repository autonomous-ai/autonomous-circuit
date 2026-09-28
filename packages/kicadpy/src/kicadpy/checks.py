"""Native report parsing: no v1 converter noise floor."""
import json
from . import toolchain
from .project import revision, write_json


def parse_reports(drc, erc):
    for key in ('violations', 'unconnected_items', 'schematic_parity', 'ignored_checks'):
        if not isinstance(drc.get(key), list):
            raise ValueError('incomplete DRC report: ' + key)
    if not isinstance(erc.get('sheets'), list) or not erc['sheets'] or not isinstance(erc.get('ignored_checks'), list):
        raise ValueError('incomplete ERC report')
    findings = []
    for key in ('violations', 'unconnected_items', 'schematic_parity'):
        findings += [dict(item, stage='drc', category=key) for item in drc[key]]
    for sheet in erc['sheets']:
        if not isinstance(sheet.get('violations'), list):
            raise ValueError('missing sheet violations')
        findings += [dict(item, stage='erc') for item in sheet['violations']]
    if any(item.get('severity') not in ('error', 'warning', 'exclusion', 'info') for item in findings):
        raise ValueError('unknown finding severity')
    return findings


def check(root, stem, output):
    versions = toolchain.versions()
    cli = toolchain.executable('cli')
    output.mkdir(exist_ok=True)
    # Refill produces the revision to check. A second read-only DRC report
    # ensures findings belong to the saved file, not an earlier in-memory board.
    toolchain.run([cli, 'pcb', 'drc', '--refill-zones', '--save-board', '--units', 'mm', '--format', 'json',
                   '-o', str(output / 'refill.json'), str(root / (stem + '.kicad_pcb'))])
    rev = revision(root)
    toolchain.run([cli, 'pcb', 'drc', '--schematic-parity', '--severity-all', '--units', 'mm', '--format', 'json',
                   '-o', str(output / 'drc.json'), str(root / (stem + '.kicad_pcb'))])
    toolchain.run([cli, 'sch', 'erc', '--severity-all', '--units', 'mm', '--format', 'json',
                   '-o', str(output / 'erc.json'), str(root / (stem + '.kicad_sch'))])
    drc, erc = (json.loads((output / name).read_text()) for name in ('drc.json', 'erc.json'))
    findings = parse_reports(drc, erc)
    if revision(root) != rev:
        raise ValueError('revision changed during checks')
    result = {'revision': rev, 'tools': versions, 'findings': findings,
              'passed': not findings, 'ignoredChecks': {'drc': drc['ignored_checks'], 'erc': erc['ignored_checks']},
              'coverage': {'erc': True, 'drc': True, 'parity': True, 'engineering': False},
              'fabricationReady': False, 'assemblyReady': False, 'hardwareTested': False}
    write_json(output / 'check.json', result)
    return result
