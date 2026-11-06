"""Explicit controlled failures on fresh asset copies; real model calls and live metadata."""
import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch
import truststore
from sragent_spatial.multiagent import SpatialSession, run_multiagent

truststore.inject_into_ssl()
source = Path('E:/SRAgent-spatial/biostudies-lung-20261106')
assets = Path('E:/SRAgent-spatial/multiagent-repair-assets-20261106')
out = Path('E:/SRAgent-spatial/multiagent-repair-20261106')
assert not assets.exists() and not out.exists()
assets.mkdir()
base = source / 'S-BSST1410/Visium'
dest = assets / 'S-BSST1410/Visium'
shutil.copytree(base / '.zip-cache', dest / '.zip-cache')
for directory in ('6ee840650637ccc8', '9942bf0a017c310a'):
    target = dest / directory
    target.mkdir()
    for name in ('filtered_feature_bc_matrix.h5', 'tissue_positions_list.csv',
                 'scalefactors_json.json', 'tissue_lowres_image.png'):
        shutil.copyfile(base / directory / name, target / name)
# No metadata cache copied: this acceptance queries the official service live.
faults = []
original_prepare = SpatialSession.prepare_files
original_validate = SpatialSession.validate_sample

def prepare(session, accession, platform):
    if accession == 'S-BSST1410' and not any(x['kind'] == 'transient_prepare_error' for x in faults):
        faults.append({'kind': 'transient_prepare_error', 'scope': 'controlled fault injection, not an observed server failure'})
        raise TimeoutError('CONTROLLED ACCEPTANCE FAULT: one transient preparation error; retry allowed')
    return original_prepare(session, accession, platform)

def validate(session, sample_id, platform):
    if not any(x['kind'] == 'missing_scale_file' for x in faults):
        path = dest / sample_id.split('--')[1] / 'scalefactors_json.json'
        assert path.resolve().is_relative_to(assets.resolve())
        path.unlink()
        faults.append({'kind': 'missing_scale_file', 'path': str(path.relative_to(assets)),
                       'scope': 'controlled deletion of acceptance copy; original E drive files untouched'})
    return original_validate(session, sample_id, platform)

with patch.object(SpatialSession, 'prepare_files', prepare), patch.object(SpatialSession, 'validate_sample', validate):
    result = asyncio.run(run_multiagent('Homo sapiens', 'lung', ['Visium'], out, assets=assets,
        model='deepseek-flash', budget=128*1024**2, max_studies=3, slices=2))
(out / 'controlled_faults.json').write_text(json.dumps(faults, indent=2))
print(json.dumps({'status': result['status'], 'workflow_complete': result['workflow_complete'],
                  'totals': result['totals'], 'agent': result['agent'], 'controlled_faults': faults}))
