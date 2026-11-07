import asyncio
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from sragent_spatial.research_request import ResearchRequest, condition_evidence, tissue_matches, parse_request
from sragent_spatial.research_assets import AssetPreparer, checked_url, validate_group
from sragent_spatial.research_session import ResearchSession

URL='https://ftp.ncbi.nlm.nih.gov/geo/samples/GSM999nnn/GSM999001/suppl/'

class ConditionTests(unittest.TestCase):
    def test_diseased_accepts_sample_diagnosis_and_tumors_only(self):
        request = ResearchRequest('Homo sapiens','kidney',('Xenium',),sample_state='diseased')
        for label, status in [('AIN','eligible'),('carcinoma','eligible'),('non-diseased','out_of_scope'),('N/A','unknown')]:
            result = condition_evidence({'Sample_characteristics_ch1':['disease: '+label]},request,URL)
            self.assertEqual(result['status'],status)

    def test_tumor_normal_adjacent_mixed_unknown_and_study_scope(self):
        request=ResearchRequest('Homo sapiens','pancreas',('Xenium',),'pancreatic cancer','tumor')
        cases=[({'diagnosis':['PDAC']},'eligible'),({'state':['Healthy'],'Sample_title':['pancreatic cancer cohort']},'out_of_scope'),
               ({'diagnosis':['PDAC'],'state':['adjacent']},'out_of_scope'),
               ({'state':['tumor and normal tissue pooled'],'diagnosis':['PDAC']},'mixed'),
               ({'state':['tumor and adjacent tissue pooled'],'diagnosis':['PDAC']},'mixed'),
               ({'Sample_title':['Pancreas section']},'unknown'),({'diagnosis':['no cancer']},'out_of_scope')]
        for fields,expected in cases:
            with self.subTest(fields=fields):
                result=condition_evidence(fields,request,URL)
                self.assertEqual(result['status'],expected)
                for evidence in result['evidence']:
                    self.assertIn(evidence['quote'],fields[evidence['field']])
        self.assertEqual(condition_evidence({'diagnosis':['PDAC']},request,URL,scope='study')['status'],'unknown')
        adjacent=ResearchRequest('Homo sapiens','pancreas',('Xenium',),'pancreatic cancer','adjacent')
        self.assertEqual(condition_evidence({'diagnosis':['PDAC'],'state':['adjacent']},adjacent,URL)['status'],'eligible')
        self.assertTrue(tissue_matches('pancreas','human pancreatic cancer'))
        self.assertTrue(tissue_matches('kidney','renal cortex'))
        self.assertFalse(tissue_matches('lung','brain metastasis'))
        generic=ResearchRequest('Homo sapiens','pancreas',('Xenium',),'cancer','tumor')
        self.assertEqual(condition_evidence({'Sample_title':['Pancreas Xenium Human Multi-Tissue and Cancer Panel']},generic,URL)['status'],'unknown')

    def test_model_request_schema_is_checked(self):
        data={'organism':'Homo sapiens','tissue':'pancreas','platforms':['Xenium'],'disease':'pancreatic cancer','sample_state':'tumor'}
        client=SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(data))))
        with patch('sragent_spatial.agents.configure',return_value=(None,lambda **k:client,None,None)):
            request,record=asyncio.run(parse_request('human pancreatic cancer Xenium','test-model'))
            self.assertEqual(request.sample_state,'tumor')
            self.assertEqual(record['normalized']['disease'],'pancreatic cancer')
            client.ainvoke.return_value=SimpleNamespace(content=json.dumps({**data,'platforms':[123]}))
            with self.assertRaises(ValueError):
                asyncio.run(parse_request('test','test-model'))


class AssetTests(unittest.TestCase):
    def write_tar(self,path,files):
        with tarfile.open(path,'w:gz') as archive:
            for name,body in files.items():
                info=tarfile.TarInfo(name)
                info.size=len(body)
                archive.addfile(info,io.BytesIO(body))

    def visium_files(self,root):
        import h5py
        from PIL import Image
        matrix=root/'scratch.h5'
        with h5py.File(matrix,'w') as stream:
            m=stream.create_group('matrix')
            m['shape']=[2,2]; m['barcodes']=[b'a',b'b']
            m['data']=[1,2]; m['indices']=[0,1]; m['indptr']=[0,1,2]
        image=io.BytesIO(); Image.new('RGB',(10,10)).save(image,format='PNG')
        return {'filtered_feature_bc_matrix.h5':matrix.read_bytes(),
            'spatial/tissue_positions_list.csv':b'a,1,0,0,1,2\nb,1,0,1,2,3\n',
            'spatial/scalefactors_json.json':b'{"tissue_lowres_scalef":1}',
            'spatial/tissue_lowres_image.png':image.getvalue()}

    def test_tar_slices_and_missing_images_do_not_block_the_complete_slice(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); files=self.visium_files(root)
            source=root/'GSM999001_bundle.tar.gz'
            self.write_tar(source,{**{'sliceA/'+k:v for k,v in files.items()},
                **{'sliceB/'+k:v for k,v in files.items() if 'image' not in k}})
            result=AssetPreparer(root/'out',[root],0).prepare([{'url':URL+source.name}],'GSM999001','Visium')
            self.assertEqual([r['status'] for r in result['groups']],['files_prepared','missing_files'])
            self.assertEqual(validate_group(result['groups'][0],'Visium',root/'check')['status'],'correspondence_passed')

    def test_cross_package_merge_requires_an_explicit_pairing_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); files=self.visium_files(root)
            first=root/'GSM999001_expression.tar.gz'; second=root/'GSM999001_spatial.tar.gz'
            self.write_tar(first,{k:v for k,v in files.items() if k.endswith('.h5')})
            self.write_tar(second,{k:v for k,v in files.items() if not k.endswith('.h5')})
            assets=[{'url':URL+p.name} for p in (first,second)]
            unknown=AssetPreparer(root/'unknown',[root],0).prepare(assets,'GSM999001','Visium')
            self.assertTrue(all(r['status']=='missing_files' for r in unknown['groups']))
            paired=AssetPreparer(root/'paired',[root],0).prepare([{**a,'pairing_key':'explicit-section-A'} for a in assets],'GSM999001','Visium')
            self.assertEqual(len(paired['groups']),1)
            self.assertEqual(validate_group(paired['groups'][0],'Visium',root/'check')['status'],'correspondence_passed')

    def test_unsafe_archive_and_private_download_are_rejected(self):
        with self.assertRaises(ValueError): checked_url('http://127.0.0.1/secret')
        with self.assertRaises(ValueError): checked_url('https://ftp.ebi.ac.uk/../outside')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'GSM999001_bad.tar.gz'
            self.write_tar(source,{'../../filtered_feature_bc_matrix.h5':b'bad'})
            result=AssetPreparer(root/'out',[root],0).prepare([{'url':URL+source.name}],'GSM999001','Visium')
            self.assertTrue(result['errors'])
            self.assertFalse((root/'filtered_feature_bc_matrix.h5').exists())

    def test_xenium_tar_uses_actual_cell_and_pixel_mapping_checks(self):
        import test_local_validation as fixtures
        if not fixtures.READY: self.skipTest('Xenium extras needed')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            spec,_=fixtures.XeniumValidationTests().fixture(root)
            source=root/'GSM999001_xenium.tar.gz'
            self.write_tar(source,{'outs/'+p.name:p.read_bytes() for p in Path(spec['data_dir']).iterdir()})
            result=AssetPreparer(root/'out',[root],0).prepare([{'url':URL+source.name}],'GSM999001','Xenium')
            with patch('sragent_spatial.xenium_validation.plot_preview'):
                checked=validate_group(result['groups'][0],'Xenium',root/'check')
            self.assertEqual(checked['status'],'correspondence_passed')
            self.assertTrue(checked['checks']['pixel_size_agrees_with_ome'])

    def test_hd_zip_keeps_2_and_16_um_bins_separate(self):
        import h5py
        import pyarrow as pa
        import pyarrow.parquet as pq
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); files=self.visium_files(root)
            archive=root/'GSM999001_hd.zip'
            with zipfile.ZipFile(archive,'w') as zipped:
                for size in (2,16):
                    ids=[f's_{size:03d}um_00001_00001-1',f's_{size:03d}um_00001_00002-1']
                    path=root/f'matrix{size}.h5'
                    with h5py.File(path,'w') as stream:
                        m=stream.create_group('matrix'); m['shape']=[2,2]
                        m['barcodes']=[s.encode() for s in ids]; m['data']=[1,2]
                        m['indices']=[0,1]; m['indptr']=[0,1,2]
                    positions=root/f'positions{size}.parquet'
                    pq.write_table(pa.table({'barcode':ids,'in_tissue':[1,1],'array_row':[1,1],
                        'array_col':[1,2],'pxl_row_in_fullres':[1.,2.],'pxl_col_in_fullres':[2.,3.]}),positions)
                    prefix=f'binned_outputs/square_{size:03d}um/'
                    zipped.writestr(prefix+'filtered_feature_bc_matrix.h5',path.read_bytes())
                    zipped.writestr(prefix+'spatial/tissue_positions.parquet',positions.read_bytes())
                    zipped.writestr(prefix+'spatial/scalefactors_json.json',json.dumps({'bin_size_um':size,'tissue_lowres_scalef':1}))
                    zipped.writestr(prefix+'spatial/tissue_lowres_image.png',files['spatial/tissue_lowres_image.png'])
            prepared=AssetPreparer(root/'out',[root],0).prepare([{'url':URL+archive.name}],'GSM999001','Visium HD')
            self.assertEqual(len(prepared['groups']),2)
            for i,group in enumerate(prepared['groups']):
                self.assertEqual(validate_group(group,'Visium HD',root/f'check{i}')['status'],'correspondence_passed')

    def test_full_mtx_bundle_reads_features_and_all_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); files=self.visium_files(root)
            files.pop('filtered_feature_bc_matrix.h5')
            files.update({'filtered_feature_bc_matrix/barcodes.tsv':b'a\nb\n',
                'filtered_feature_bc_matrix/features.tsv':b'g1\tG1\ng2\tG2\n',
                'filtered_feature_bc_matrix/matrix.mtx':b'%%MatrixMarket matrix coordinate integer general\n2 2 2\n1 1 1\n2 2 2\n'})
            source=root/'GSM999001_mtx.tar.gz'; self.write_tar(source,files)
            group=AssetPreparer(root/'out',[root],0).prepare([{'url':URL+source.name}],'GSM999001','Visium')['groups'][0]
            result=validate_group(group,'Visium',root/'check')
            self.assertEqual(result['status'],'correspondence_passed')
            self.assertEqual(result['checks']['matrix_content']['read_entries'],2)

    def test_compressed_h5_and_png_are_bounded_and_verified_after_unwrapping(self):
        import gzip
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); files=self.visium_files(root)
            files['filtered_feature_bc_matrix.h5.gz']=gzip.compress(files.pop('filtered_feature_bc_matrix.h5'))
            files['spatial/tissue_lowres_image.png.gz']=gzip.compress(files.pop('spatial/tissue_lowres_image.png'))
            source=root/'GSM999001_compressed.tar.gz'; self.write_tar(source,files)
            group=AssetPreparer(root/'out',[root],0).prepare([{'url':URL+source.name}],'GSM999001','Visium')['groups'][0]
            self.assertTrue(group['files']['h5']['compressed_source_sha256'])
            self.assertEqual(validate_group(group,'Visium',root/'check')['status'],'correspondence_passed')

class ResearchSessionTests(unittest.TestCase):
    def make_session(self,root):
        (root/'out').mkdir()
        return ResearchSession('Homo sapiens','pancreas',['Visium'],root/'out',root/'assets',0,2,2,
            request=ResearchRequest('Homo sapiens','pancreas',('Visium',),'cancer','tumor'),sources=['biostudies'])

    def test_only_advertised_official_external_manifests_are_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            session=self.make_session(Path(folder))
            token=session.add('biostudies','Visium','S-BSST100')
            session.entries[token].update(selected=True,reviewed=True,study={'section':{'attributes':[
                {'name':'File List','value':'files.json'}]}})
            info={'httpLink':'https://ftp.ebi.ac.uk/biostudies/fire/S-BSST/100/S-BSST100','isPublic':True}
            with patch.object(session.client,'json',return_value=info), patch.object(session.client,'get',return_value=json.dumps([
                {'path':'sectionA.tar.gz','size':10,'pairing_key':'sectionA'}]).encode()):
                result=session.inspect_files(token,'Visium')
            self.assertEqual(len(result['assets']),1)
            self.assertEqual(result['assets'][0]['pairing_key'],'sectionA')
            with patch.object(session.client,'json',return_value=info), patch.object(session.client,'get',return_value=json.dumps([
                {'url':'http://127.0.0.1/private'}]).encode()):
                result=session.inspect_files(token,'Visium')
            self.assertTrue(result['manifest_errors'])

    def test_terminal_preparation_error_is_recorded_and_cannot_be_a_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            session=self.make_session(Path(folder))
            token=session.add('biostudies','Visium','S-BSST100')
            session.hits={'biostudies:Visium':[token]}
            row={'source':'biostudies','study_id':token,'sample_id':token,'platform':'Visium',
                 'found':True,'selected':True,'files_ready':False,'status':'metadata_selected'}
            session.samples[token]=row
            session.entries[token].update(selected=True,reviewed=True,row=row)
            with patch.object(session,'prepare_files',side_effect=TimeoutError('controlled failure')):
                results=[json.loads(session.execute('assets','prepare_files',accession=token,platform='Visium')) for _ in range(3)]
            self.assertFalse(results[-1]['retryable'])
            self.assertTrue(session.report()['workflow_complete'])
            self.assertEqual(session.report()['totals']['passed'],0)
            self.assertEqual(session.samples[token]['status'],'access_failed')

    def test_geo_expansion_clinical_filter_alias_and_report_do_not_duplicate_samples(self):
        import test_pipeline_agent as fixtures
        request=ResearchRequest('Homo sapiens','pancreas',('Xenium',),'pancreatic cancer','tumor')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'out').mkdir()
            session=ResearchSession('Homo sapiens','pancreas',['Xenium'],root/'out',root/'assets',0,2,2,
                                    request=request,sources=['geo'])
            session.geo=fixtures.Client()
            source=session.geo.soft
            session.geo.soft=lambda acc: source(acc).replace('tissue: Lung','tissue: pancreatic tissue\n!Sample_characteristics_ch1 = diagnosis: PDAC')
            session.search_catalog('Xenium','geo')
            token=next(iter(session.entries))
            result=session.read_study(token,'Xenium')
            self.assertTrue(result['samples'][0]['selected'])
            report=session.report()
            self.assertEqual(len(report['samples']),1)
            self.assertFalse(report['workflow_complete'])
            # Re-reading the child is idempotent and cannot expand it as a study.
            child=result['expanded_samples'][0]
            self.assertEqual(session.read_study(child,'Xenium')['clinical_status'],'eligible')
