import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sragent_spatial.research_assets import sha256
from sragent_spatial.research_progress import annotate_catalog, biostudies_publications, publications
from sragent_spatial.research_request import ResearchRequest, condition_evidence
from sragent_spatial.research_session import ResearchSession


class ProgressTests(unittest.TestCase):
    def session(self, root):
        out = root/'out'
        out.mkdir(exist_ok=True)
        request = ResearchRequest('Homo sapiens','lung',('Visium',))
        return ResearchSession('Homo sapiens','lung',['Visium'],out,root/'assets',0,1,2,
                               request=request,sources=['geo'])

    def test_restore_reuses_completed_steps_and_repairs_changed_files_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = self.session(root)
            candidate = session.add('geo','Visium','GSM123')
            session.entries[candidate].update(reviewed=True, selected=True, prepared=True)
            for token in ('one','two'):
                image = root/(token+'.png')
                image.write_bytes(token.encode())
                session.samples[token] = {'sample_id':token,'study_id':candidate,'candidate_id':candidate,
                    'platform':'Visium','source':'geo','status':'correspondence_passed','files_ready':True,
                    'checks':{'valid':True},'files':{'image':{'path':str(image),'sha256':sha256(image)}}}
            with patch.object(session,'search_catalog',return_value={'candidates':[candidate]}):
                session.execute('search','search_catalog',platform='Visium',source='geo')
            with patch.object(session,'validate_sample',return_value={'status':'correspondence_passed'}):
                for token in ('one','two'):
                    session.execute('validation','validate_sample',sample_id=token,platform='Visium')
            (root/'two.png').write_bytes(b'changed')
            restored = self.session(root)
            restored.restore()
            self.assertEqual(restored.samples['one']['status'],'correspondence_passed')
            self.assertEqual(restored.samples['two']['status'],'missing_or_changed_files')
            self.assertFalse(restored.entries[candidate]['prepared'])
            with patch.object(restored,'search_catalog',side_effect=AssertionError('repeat search')):
                result = json.loads(restored.execute('search','search_catalog',platform='Visium',source='geo'))
                self.assertTrue(result['reused_checkpoint'])
            with patch.object(restored,'validate_sample',return_value={'status':'correspondence_passed'}) as check:
                restored.execute('validation','validate_sample',sample_id='one',platform='Visium')
                check.assert_not_called()
                restored.execute('validation','validate_sample',sample_id='two',platform='Visium')
                check.assert_called_once()

    def test_paper_identity_alone_never_merges_distinct_sections(self):
        papers = publications({'Series_pubmed_id':['1234'],'description':'10.1234/unrelated'})
        self.assertEqual([p['id'] for p in papers],['1234'])
        image_url = 'https://www.ebi.ac.uk/biostudies/files/S-BIAD123/section.tif'
        entries = {'g':{'publications':papers,'fields':{'Sample_characteristics_ch1':[
            'microscope image: '+image_url, 'histology image: https://example.invalid/image.tif']}},'b':{'publications':papers}}
        def row(token, source, coordinate_hash):
            files = {role:{'sha256':digest} for role,digest in
                     {'image':'i','h5':'h','positions':coordinate_hash,'scalefactors':'s'}.items()}
            return {'sample_id':token,'study_id':token,'platform':'Visium','source':source,
                    'status':'correspondence_passed','files':files}
        rows = [row('g','geo','p'),row('b','biostudies','different')]
        self.assertEqual(annotate_catalog(rows,entries),[])
        rows[1]['files']['positions']['sha256'] = 'p'
        self.assertEqual(len(annotate_catalog(rows,entries)),1)
        self.assertEqual(rows[0]['canonical_sample_id'],rows[1]['canonical_sample_id'])
        self.assertEqual(rows[0]['lineage']['publications'],papers)
        self.assertEqual([i['source_url'] for i in rows[0]['lineage']['declared_images']],[image_url])
        for item in rows:
            item['matrix_scope'] = 'header_only'
        self.assertEqual(annotate_catalog(rows,entries),[])
        self.assertNotIn('canonical_sample_id',rows[0])

    def test_biostudies_publication_excludes_dataset_doi(self):
        study = {'section':{'type':'submission','attributes':[{'name':'DOI','value':'10.6019/dataset'}],
                 'subsections':[{'type':'Study','links':[{
                     'url':'https://www.biorxiv.org/content/10.1101/2023.12.21.572330v1',
                     'attributes':[{'name':'Description','value':'Study preprint manuscript'}],
                 }]}]}}
        self.assertEqual([p['id'] for p in biostudies_publications(study)],['10.1101/2023.12.21.572330'])

    def test_illegal_validation_preserves_missing_file_outcome(self):
        with TemporaryDirectory() as tmp:
            session = self.session(Path(tmp))
            session.samples['incomplete'] = {'sample_id':'incomplete','study_id':'geo:Visium:GSM1',
                'platform':'Visium','source':'geo','status':'missing_files','files_ready':False}
            result = json.loads(session.execute('validation','validate_sample',sample_id='incomplete',platform='Visium'))
            self.assertEqual(result['status'],'tool_error')
            self.assertEqual(session.samples['incomplete']['status'],'missing_files')

    def test_colon_diagnosis_is_evidence_for_colorectal_cancer(self):
        request = ResearchRequest('Homo sapiens','colon',('Xenium',),'colorectal cancer','tumor')
        url = 'https://www.ncbi.nlm.nih.gov/geo/'
        self.assertEqual(condition_evidence({'disease':'colon cancer'},request,url)['status'],'eligible')
        self.assertEqual(condition_evidence({'disease':'ulcerative colitis'},request,url)['status'],'unknown')

    def test_pooled_case_and_control_are_not_a_healthy_slice(self):
        request = ResearchRequest('Homo sapiens','skin',('Visium HD',),sample_state='normal')
        result = condition_evidence({
            'Sample_title':'Cases and Healthy Control Data',
            'Sample_data_processing':'Sample label (case or control) for each barcode',
        },request,'https://www.ncbi.nlm.nih.gov/geo/')
        self.assertEqual(result['status'],'mixed')
        self.assertEqual(len(result['evidence']),2)
