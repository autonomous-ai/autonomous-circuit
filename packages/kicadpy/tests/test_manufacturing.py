import hashlib
import json
import math
import pytest
from kicadpy.gerber_native import linearize
from kicadpy.manufacture import REVIEW_AREAS, engineering_review, assembly_rows, design_inputs, verify_positions
from kicadpy.project import manifest


def test_engineering_bound_to_inputs_and_evidence(tmp_path):
    (tmp_path/'main.kicad_pcb').write_text('source')
    (tmp_path/'engineering').mkdir()
    evidence=tmp_path/'engineering/review.md';evidence.write_text('measured design calculations')
    inputs=design_inputs(tmp_path)
    check={'status':'pass','analysis':'calculated and checked','evidence':[{'path':'engineering/review.md','sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
    report={'schemaVersion':1,'reviewer':'test','designInputs':inputs,'acceptedIgnoredChecks':{'drc':[],'erc':[]},'checks':{k:check for k in REVIEW_AREAS}}
    (tmp_path/'manufacturing.json').write_text(json.dumps(report))
    assert engineering_review(tmp_path,inputs,report['acceptedIgnoredChecks'])[0] == []
    assert 'engineering/review.md' in manifest(tmp_path)
    assert design_inputs(tmp_path) == inputs
    evidence.write_text('changed')
    assert any(f['kind']=='engineering_evidence' for f in engineering_review(tmp_path,inputs,report['acceptedIgnoredChecks'])[0])
    (tmp_path/'main.kicad_pcb').write_text('changed source')
    assert any(f['kind']=='engineering_stale' for f in engineering_review(tmp_path,design_inputs(tmp_path),report['acceptedIgnoredChecks'])[0])


def sample():
    state={'footprints':[{'reference':'R1','uuid':'r','value':'1k','footprint':'0603','dnp':False,'excludedFromBOM':False,'excludedFromPosition':False,'at':[12,23],'layer':'B.Cu','rotationDeg':90}]}
    parts={'parts':[{'refdes':['R1'],'mpn':'exact','manufacturer':'maker','native_footprint':'lib:0603','lcsc':'C123'}]}
    return state,parts


def test_assembly_requires_explicit_identity_population_and_rotation():
    state,parts=sample()
    findings,*_=assembly_rows(state,parts,{})
    assert {f['kind'] for f in findings} == {'assembly_method','assembly_rotation'}
    review={'assembly':{'R1':{'method':'factory','rotationOffsetDeg':-90}}}
    findings,bom,cpl,manual=assembly_rows(state,parts,review)
    assert findings == [] and not manual
    assert cpl[0]['Mid Y']=='-23.000000' and cpl[0]['Layer']=='Bottom' and cpl[0]['Rotation']=='0.000000'
    assert {r['Designator'] for r in bom} == {r['Designator'] for r in cpl}
    parts['parts'].append(parts['parts'][0])
    assert any(f['kind']=='bom_duplicate' for f in assembly_rows(state,parts,review)[0])


def test_cpl_is_reconciled_to_native_positions(tmp_path):
    state,parts=sample();review={'assembly':{'R1':{'method':'factory','rotationOffsetDeg':-90}}}
    _,_,cpl,_=assembly_rows(state,parts,review)
    file=tmp_path/'positions.csv';file.write_text('Ref,PosX,PosY,Rot,Side\nR1,12,-23,90,bottom\n')
    assert verify_positions(file,state,cpl,review)==[]
    file.write_text('Ref,PosX,PosY,Rot,Side\nR1,12,23,90,bottom\n')
    assert verify_positions(file,state,cpl,review)[0]['kind']=='position_mismatch'


def test_arc_linearization_full_circle_and_strict_units():
    header='%FSLAX46Y46*%\n%MOMM*%\nG75*\n'
    out=linearize(header+'X1000000Y0D02*\nG03*\nX1000000Y0I-1000000J0D01*\nM02*')
    assert 'G03' not in out and out.count('D01*')>60
    import re
    points=[(int(x),int(y)) for x,y in re.findall(r'X(-?\d+)Y(-?\d+)D01',out)]
    assert points[-1]==(1000000,0)
    assert all(abs(math.hypot(x,y)-1000000)<1 for x,y in points)
    with pytest.raises(ValueError):linearize(header.replace('MOMM','MOIN'))
    with pytest.raises(ValueError):linearize(header.replace('G75','G74'))
