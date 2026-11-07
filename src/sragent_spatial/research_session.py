"""Source adapters behind the same model supervisor/search/file/validation tools."""
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .multiagent import SpatialSession
from .biostudies import MIB, file_url, nodes, study_evidence, study_files, validate_zip_study
from .geo import MetadataClient, parse_soft, record_from_soft, source_url
from .research_request import condition_evidence, tissue_matches
from .research_assets import AssetPreparer, sha256, validate_group, checked_url
from .research_progress import annotate_catalog, biostudies_publications, files_match, operation_key, publications
from .spatial_search import build_spatial_query
from .screening import screen_sample, VISIUM_HD
from .discovery import sample_packet
from .tenx_batch import checkpoint
from .unified_batch import summarize


class ResearchSession(SpatialSession):
    def __init__(self, *args, request, sources=('geo','biostudies','tenx'), roots=(), tenx_cache=None, snapshots=()):
        super().__init__(*args)
        if not sources or not set(sources) <= {'geo','biostudies','tenx'}:
            raise ValueError('Unknown research sources')
        self.request, self.sources = request, list(dict.fromkeys(sources))
        self.roots = [Path(p) for p in roots]
        self.tenx_cache = Path(tenx_cache) if tenx_cache else None
        self.snapshots = [Path(p) for p in snapshots]
        self.geo = MetadataClient(self.assets / 'geo_cache')
        self.entries, self.source_errors, self.groups, self.source_modes = {}, [], {}, {}
        self.scope_note = ('Configured sources: ' + json.dumps(self.sources) + '. Fixed research filters: ' + json.dumps(request.json())
            + '. Terminal failures, unsupported formats and unknown clinical evidence are recorded outcomes; do not loop on them.')
        self.operation_limit = 240
        self.resume_count = 0
        self.replayed = {}
        self.invalidated = []

    def save(self):
        state = {name:getattr(self,name) for name in ('hits','entries','samples','groups','usage',
                 'events','messages','source_errors','source_modes','resume_count','invalidated')}
        state.update(schema=1,attempts=[[list(key), count] for key,count in self.attempts.items()],
                     biostudies_requests=self.client.requests, geo_requests=self.geo.requests)
        checkpoint(self.out/'progress.json',state)
        checkpoint(self.out/'trace.json', {'events':self.events,'messages':self.messages,
            'state':self.overview(),'requests':self.client.requests+self.geo.requests})

    def restore(self):
        state = json.loads((self.out/'progress.json').read_text(encoding='utf8'))
        if state.get('schema') != 1:
            raise ValueError('Unsupported research checkpoint schema')
        for name in ('hits','entries','samples','groups','usage','events','messages',
                     'source_errors','source_modes','resume_count','invalidated'):
            setattr(self,name,state[name])
        self.client.requests = state['biostudies_requests']
        self.geo.requests = state['geo_requests']
        self.attempts = {tuple(key): count for key,count in state['attempts']}
        self.resume_count += 1
        affected = set()
        for token,row in self.samples.items():
            if row['status'] not in {'files_prepared','correspondence_passed','mismatch'} or files_match(row):
                continue
            candidate = row.get('candidate_id',row['study_id'])
            affected.update((token,candidate))
            self.invalidated.append(token)
            row.update(status='missing_or_changed_files',files_ready=False)
            row.pop('checks',None)
            entry = self.entries[candidate]
            entry['prepared'] = False
            entry.pop('terminal_failure',None)
        for event in self.events:
            action, args, result = event.get('action'), event.get('args',{}), event.get('result',{})
            if not action or 'finished_at_unix' not in event:
                continue
            key = operation_key(action,args)
            if action in {'prepare_files','validate_sample'} and affected.intersection(args.values()):
                self.attempts.pop(key,None)
                continue
            if not result.get('retryable') and result.get('status') not in {'tool_error','access_failed'}:
                self.replayed[key] = result
            else:
                self.replayed.pop(key,None)
        self.save()

    def execute(self, agent, action, **args):
        key = operation_key(action,args)
        if key in self.replayed:
            result = {**self.replayed[key],'reused_checkpoint':True}
            self.events.append({'sequence':len(self.events)+1,'agent':agent,'action':action,
                                'args':args,'result':result,'reused_checkpoint':True})
            self.save()
            return json.dumps(result)
        return super().execute(agent,action,**args)

    def finish_operation(self, action, args, count, result):
        if count>=3 and result.get('retryable'):
            result['retryable']=False
            result['retry_limit_reached']=True
        if action=='prepare_files' and (result.get('status')=='tool_error' and not result.get('retryable')
                or count>=3 and not self.entries.get(args.get('accession'),{}).get('prepared')):
            token=args.get('accession')
            entry=self.entries.get(token)
            if entry:
                entry['terminal_failure']=True
                if token in self.samples and self.samples[token]['status']=='metadata_selected':
                    self.samples[token].update(status='access_failed',reason=result.get('reason','preparation retries exhausted'))
        if action=='validate_sample' and result.get('status')=='tool_error' and not result.get('retryable'):
            token=args.get('sample_id')
            row = self.samples.get(token)
            if row and row['platform']==args.get('platform') and row['status']=='files_prepared':
                row.update(status='validation_error',reason=result.get('reason'))
        return result

    def overview(self):
        return {'filters': self.request.json(), 'sources': self.sources, 'searched': list(self.hits),
            'candidates': [{'study_id': key, 'platform': e['platform'], 'source': e['source'],
                'reviewed': e.get('reviewed',False), 'selected': e.get('selected'),
                'inspected': e.get('inspected',False), 'prepared': e.get('prepared',False),
                'terminal_failure':e.get('terminal_failure',False)}
                for key,e in self.entries.items()],
            'samples': [{k: row.get(k) for k in ('sample_id','study_id','platform','status','clinical_status')}
                        for row in self.samples.values()], 'source_errors': self.source_errors}

    def add(self, source, platform, accession, **data):
        token = source + ':' + platform + ':' + accession
        self.entries.setdefault(token, {'source':source,'platform':platform,'accession':accession,**data})
        return token

    def search_catalog(self, platform, source='biostudies'):
        if source not in self.sources or platform not in self.platforms:
            raise ValueError('Search outside fixed source/platform scope')
        search_key = source + ':' + platform
        ids = []
        try:
            if source == 'geo':
                query = build_spatial_query(self.organism, self.tissue, [platform])
                if self.request.disease:
                    query += ' AND "' + self.request.disease + '"[All Fields]'
                found = self.geo.get_json('esearch.fcgi', db='gds', term=query, retmode='json', retmax=self.max_studies)
                uids = found['esearchresult']['idlist']
                details = self.geo.get_json('esummary.fcgi',db='gds',id=','.join(uids),retmode='json')['result'] if uids else {}
                for uid in uids:
                    acc = details.get(uid,{}).get('accession','')
                    if re.fullmatch(r'GSE[1-9][0-9]*',acc):
                        ids.append(self.add(source,platform,acc))
            elif source == 'biostudies':
                values = [platform, self.tissue, self.request.disease]
                query = ' AND '.join('"'+v+'"' for v in values if v and v!='*')
                found = self.client.json('/search',query=query,pageSize=self.max_studies)
                for hit in found['hits'][:self.max_studies]:
                    from .biostudies import ACCESSION
                    if hit.get('isPublic') is True and ACCESSION.fullmatch(hit['accession']):
                        ids.append(self.add(source,platform,hit['accession']))
            else:
                from .tenx_online import read_catalog_snapshot, discover_10x
                from .deepseek_model import api_key
                from .tenx_index import MODE as INDEX_MODE, discover_indexed_human, official_catalog_status
                rows = []
                catalog_mode = 'explicit_snapshot_or_registered_cache'
                coverage = bool(self.tenx_cache and platform=='Xenium')
                if self.tenx_cache and platform == 'Xenium':
                    records = json.loads((self.tenx_cache/'sample_metadata.json').read_text(encoding='utf-8-sig'))['samples']
                    manifest = json.loads((self.tenx_cache/'download_manifest.json').read_text(encoding='utf-8-sig'))
                    for record in records:
                        if record.get('source')!='10X' or (self.tissue!='*' and record.get('organ','').casefold()!=self.tissue.casefold()):
                            continue
                        species_names={'Homo sapiens':'human','Mus musculus':'mouse','Rattus norvegicus':'rat'}
                        species={name for name in species_names.values() if re.search(r'\b'+name+r'\b',record.get('dataset',''),re.I)}
                        if self.organism!='*' and species!={species_names.get(self.organism,self.organism.casefold())}:
                            continue
                        for item in manifest:
                            match = re.fullmatch(r'samples/([A-Za-z0-9_-]+)/morphology_focus\.ome\.tif',item.get('relative',''))
                            if item.get('row')==record.get('row') and match and item.get('type')=='zip':
                                rows.append({**record,'sample_id':match[1], 'source_url':record['url'],
                                    'catalog_mode':'registered_10x_cache_snapshot','cache_entry':True})
                for path in self.snapshots:
                    snapshot=json.loads(path.read_text(encoding='utf-8-sig'))
                    coverage=coverage or any(r.get('platform')==platform for r in snapshot['samples'])
                    rows.extend({**r,'catalog_mode':'explicit_catalog_snapshot'} for r in
                        read_catalog_snapshot(path,self.organism,self.tissue,[platform],self.max_studies))
                if not self.tenx_cache and not self.snapshots:
                    if self.organism.casefold() in {'homo sapiens','human'} and api_key():
                        direct = official_catalog_status()
                        if direct['status']=='access_failed':
                            self.source_errors.append({'source':'tenx','platform':platform,
                                'mode':'direct_official_catalog_probe',**direct})
                        rows = discover_indexed_human(self.tissue,platform,self.max_studies,disease=self.request.disease)
                        catalog_mode = INDEX_MODE
                    else:
                        rows = discover_10x(self.organism,self.tissue,[platform],self.max_studies)
                        rows = [{**r,'catalog_mode':'live'} for r in rows]
                        catalog_mode = 'live'
                elif not coverage:
                    self.hits[search_key]=[]
                    error={'source':'tenx','platform':platform,'status':'catalog_not_configured',
                           'reason':'Configured cache/snapshots do not cover this platform; no live search claimed'}
                    self.source_errors.append(error)
                    return error
                seen = set()
                for row in rows:
                    if row['sample_id'] in seen:
                        continue
                    seen.add(row['sample_id'])
                    ids.append(self.add(source,platform,row['sample_id'],catalog=row))
                    if len(ids)>=self.max_studies:
                        break
                query = {'organism':self.organism,'tissue':self.tissue,'platform':platform}
            self.hits[search_key] = ids
            self.source_modes[search_key] = 'live_or_verified_metadata_cache' if source!='tenx' else catalog_mode
            return {'source':source,'query':query,'candidates':ids,'limit':self.max_studies,
                    'source_modes':[self.entries[key].get('catalog',{}).get('catalog_mode') for key in ids]}
        except (OSError,ValueError,KeyError,RuntimeError,ImportError) as exc:
            self.hits[search_key] = []
            error = {'source':source,'platform':platform,'status':'access_failed','error_type':type(exc).__name__,'reason':str(exc)}
            self.source_errors.append(error)
            return {**error,'retryable':False}

    def clinical(self, fields, url, scope='sample'):
        return condition_evidence(fields,self.request,url,scope=scope)

    def record(self, token, entry, fields, url, base_selected):
        condition = self.clinical(fields,url)
        selected = bool(base_selected and condition['status'] in {'eligible','not_required'})
        row = {'source':entry['source'],'study_id':entry.get('parent',token),'sample_id':token,
               'platform':entry['platform'],'source_url':url,'found':True,'selected':selected,'files_ready':False,
               'clinical_status':condition['status'],'condition_evidence':condition,
               'scope_supported':bool(base_selected),
               'status':'metadata_selected' if selected else 'not_selected'}
        self.samples[token] = row
        return row

    def read_study(self, accession, platform):
        entry = self.entries.get(accession)
        if not entry or entry['platform']!=platform:
            raise ValueError('Read only discovered candidate tokens')
        source, acc = entry['source'],entry['accession']
        if source=='geo' and acc.startswith('GSM') and entry.get('reviewed'):
            return entry['row']
        if source=='geo':
            fields = parse_soft(self.geo.soft(acc),acc)
            entry['publications'] = publications(fields)
            listed = fields.get('Series_sample_id',[])
            children = []
            for sample in listed[:self.slices]:
                child = self.add('geo',platform,sample,parent=accession)
                child_entry = self.entries[child]
                if sample not in listed or not re.fullmatch(r'GSM[1-9][0-9]*',sample):
                    raise ValueError('Invalid GEO sample association')
                sample_fields = parse_soft(self.geo.soft(sample),sample)
                if acc not in sample_fields.get('Sample_series_id',[]):
                    raise ValueError('Sample does not link back to discovered study')
                packet = sample_packet(sample_fields,sample)
                packet['screen_scope'] = {'organism':self.organism,'tissue':self.tissue}
                rules = screen_sample(packet)
                platforms = rules['platform_candidates']
                if any(VISIUM_HD.search(v) for values in packet['fields'].values() for v in values):
                    platforms = ['Visium HD']
                organisms={v.casefold() for k,vs in packet['fields'].items() if k.startswith('Sample_organism') for v in vs}
                species_ok=bool(organisms) and len(organisms)==1 and (self.organism=='*' or
                    self.organism.casefold() in organisms or self.organism=='Homo sapiens' and organisms=={'human'})
                row = self.record(child,child_entry,packet['fields'],source_url(sample),
                    species_ok and tissue_matches(self.tissue,rules.get('tissue_text') or '') and platforms==[platform])
                row['scope_evidence']=rules['evidence']
                child_entry.update(reviewed=True,selected=row['selected'],fields=sample_fields,
                    assets=record_from_soft(sample_fields,sample)['assets'],row=row)
                children.append(child)
            entry.update(reviewed=True,selected=False,expanded=True,children=children,
                         not_read_due_to_limit=listed[self.slices:])
            return {'study_id':accession,'expanded_samples':children,'samples':[self.samples[k] for k in children],
                    'not_read_due_to_limit':len(listed[self.slices:])}
        if source=='biostudies':
            study = self.client.json('/studies/'+acc)
            if study.get('accno')!=acc:
                raise ValueError('Study identity mismatch')
            evidence = study_evidence(study,self.organism,self.tissue,platform)
            entry.update(study=study,reviewed=True,selected=evidence['matches'],metadata_evidence=evidence)
            entry['publications'] = biostudies_publications(study)
            # A selected study is only a candidate. Sample diagnosis is checked before preparing it.
            row = {'source':'biostudies','study_id':accession,'sample_id':accession,'platform':platform,
                   'found':True,'selected':False,'files_ready':False,'status':'study_candidate' if evidence['matches'] else 'not_selected',
                   'metadata_evidence':evidence, 'source_url':'https://www.ebi.ac.uk/biostudies/studies/'+acc}
            entry['row'] = row
            return {'study_id':accession,'selected_candidate':evidence['matches'],'evidence':evidence}
        catalog = entry['catalog']
        url = catalog['source_url']
        if not url.startswith('https://www.10xgenomics.com/datasets/'):
            raise ValueError('Invalid official catalog record')
        fields = {k:[str(v)] for k,v in catalog.items() if k in {'dataset','title','disease','state','description'}}
        # Dataset cards and registered cache rows identify one published sample;
        # snapshot origin remains explicit and is never labelled a live page read.
        fields['Sample_title'] = [catalog.get('dataset',catalog.get('title',''))]
        row = self.record(accession,entry,fields,url,not catalog.get('catalog_error'))
        row['catalog_mode'] = catalog['catalog_mode']
        if catalog.get('indexed_url'):
            row['catalog_evidence'] = {k:catalog[k] for k in
                ('indexed_url','metadata_evidence','asset_evidence') if k in catalog}
        entry.update(reviewed=True,selected=row['selected'],row=row)
        entry['publications'] = publications(catalog)
        return row

    def metadata_tables(self, entry):
        acc = entry['accession']
        files = study_files(entry['study'])
        tables = [f for f in files if f['path'].lower().endswith(('.tsv','.csv','.sdrf.txt'))
                  and re.search(r'metadata|sample|sdrf',f['path'],re.I) and 0<f['size']<=4*MIB]
        records = []
        for item in tables[:4]:
            url = file_url(entry['info'],acc,item['path'])
            body = self.client.get(url,limit=4*MIB)
            reader = csv.DictReader(io.StringIO(body.decode('utf-8-sig'),newline=None),
                                    delimiter=',' if item['path'].endswith('.csv') else '\t')
            for index, record in enumerate(reader):
                if index>=1000:
                    break
                condition = self.clinical({k:[v] for k,v in record.items() if k and isinstance(v,str)},url)
                # Identity/file columns establish the association. No substring matching.
                ids = {v.strip() for k,v in record.items() if k and isinstance(v,str) and v.strip()
                       and re.search(r'sample.?id|sample.?name|source name|array data file|image file|file.?name',k,re.I)}
                ids.update(unquote(urlsplit(v).path.rsplit('/',1)[-1]) for k,v in record.items()
                    if k and isinstance(v,str) and re.search(r'file',k,re.I) and urlsplit(v).scheme in {'https','ftp'})
                records.append({'ids':sorted(ids),'fields':record,'condition':condition,'source_url':url,'sha256':hashlib.sha256(body).hexdigest()})
        return records

    def inspect_files(self, accession, platform):
        entry = self.entries.get(accession)
        if not entry or not entry.get('selected') or entry['platform']!=platform:
            raise ValueError('Inspect only a screened candidate/sample')
        source = entry['source']
        if source=='biostudies':
            info = self.client.json('/studies/'+entry['accession']+'/info')
            if info.get('isPublic') is not True:
                raise ValueError('Study is not public')
            entry['info'] = info
            entry['assets'] = [{'url':file_url(info,entry['accession'],f['path']),'size':f['size']} for f in study_files(entry['study'])]
            manifest_errors=[]
            for node in nodes(entry['study']):
                for attribute in node.get('attributes',[]):
                    if attribute.get('name','').casefold()!='file list':
                        continue
                    name=attribute.get('value','')
                    try:
                        # Resolve only a manifest explicitly advertised by the official study.
                        url=file_url(info,entry['accession'],name)
                        body=self.client.get(url,limit=4*MIB)
                        data=json.loads(body)
                        records=data if isinstance(data,list) else data.get('files',[])
                        if not isinstance(records,list) or len(records)>10000:
                            raise ValueError('Unsupported/oversized external file manifest')
                        for record in records:
                            if not isinstance(record,dict):
                                raise ValueError('Unsupported external manifest row')
                            if record.get('url'):
                                asset_url=checked_url(record['url'])
                            elif record.get('path'):
                                # Use the same path validation as inline official study file records.
                                checked=study_files({'section':{'files':[record]}})[0]
                                asset_url=file_url(info,entry['accession'],checked['path'])
                            else:
                                continue
                            if not any(a['url']==asset_url for a in entry['assets']):
                                entry['assets'].append({'url':asset_url,'size':int(record.get('size',0)),
                                    'manifest_url':url,'manifest_sha256':hashlib.sha256(body).hexdigest(),
                                    'pairing_key':record.get('pairing_key')})
                    except (OSError,ValueError,KeyError,TypeError) as exc:
                        manifest_errors.append({'manifest':name,'reason':str(exc)})
            entry['manifest_errors']=manifest_errors
            entry['clinical_rows'] = self.metadata_tables(entry)
        elif source=='tenx':
            catalog = entry['catalog']
            entry['assets'] = [{'url':url} for urls in catalog.get('archives',{}).values() for url in urls]
        entry['inspected'] = True
        return {'study_id':accession,'assets':entry.get('assets',[]),
                'clinical_sample_rows':[{**r['condition'],'ids':sorted(r['ids'])} for r in entry.get('clinical_rows',[])],
                'cache_companion_mode':entry.get('catalog',{}).get('cache_entry',False),
                'manifest_errors':entry.get('manifest_errors',[])}

    def anchor_condition(self, entry, anchor):
        identifiers = set(Path(anchor).parts)
        matches = [r for r in entry.get('clinical_rows',[]) if identifiers.intersection(r['ids'])]
        if len(matches)==1:
            return matches[0]['condition']
        if not self.request.disease and self.request.sample_state=='any':
            return {'status':'not_required','evidence':[],'scope':'sample'}
        return {'status':'mixed' if len(matches)>1 else 'unknown','evidence':[],
                'reason':'no unique sample/file association in source metadata','scope':'sample'}

    def prepare_files(self, accession, platform):
        entry = self.entries.get(accession)
        if not entry or entry['platform']!=platform or not entry.get('selected') or not entry.get('inspected'):
            raise ValueError('Read and inspect this selected sample before preparation')
        target = self.out / 'prepared' / hashlib.sha256(accession.encode()).hexdigest()[:16]
        target.mkdir(parents=True,exist_ok=True)
        remaining = self.budget-self.usage.get(accession,0)
        rows = []
        if entry['source']=='tenx' and entry['catalog'].get('cache_entry'):
            from .tenx_cache import cache_entry, prepare
            import shutil
            source, image = cache_entry(self.tenx_cache,entry['accession'])
            if entry['catalog'].get('bundle_url') and entry['catalog']['bundle_url']!=source['url']:
                raise ValueError('Catalog bundle and registered image archive disagree')
            common=self.assets/'tenx'/entry['accession']
            common.mkdir(parents=True,exist_ok=True)
            required=('cell_feature_matrix.h5','cells.csv.gz','experiment.xenium')
            if not all((common/name).is_file() for name in required):
                for root in self.roots:
                    for index,path in enumerate(root.rglob('provenance.json')):
                        if index>2000: break
                        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size>MIB: continue
                        try:
                            prior=json.loads(path.read_text(encoding='utf8'))
                        except (OSError,ValueError):
                            continue
                        if prior.get('archive_url')==source['url'] and prior.get('image',{}).get('crc32')==source['crc32']:
                            for name in required:
                                candidate=path.parent/name
                                if not (common/name).exists() and candidate.is_file() and candidate.stat().st_size<=64*MIB:
                                    shutil.copyfile(candidate,common/name)
                            if (path.parent/'.zip-cache').is_dir() and not (common/'.zip-cache').exists():
                                shutil.copytree(path.parent/'.zip-cache',common/'.zip-cache')
                            break
            provenance = prepare(source,image,common,max_bytes=remaining,cache=common/'.zip-cache')
            mapping = {'h5':common/'cell_feature_matrix.h5','cells':common/'cells.csv.gz',
                       'experiment':common/'experiment.xenium','image':image}
            files = {role:{'path':str(path),'sha256':sha256(path),'bytes':path.stat().st_size} for role,path in mapping.items()}
            self.usage[accession] = self.usage.get(accession,0)+provenance['zip_range_bytes']
            rows = [{'anchor':entry['accession'],'status':'files_prepared','files':files,'provenance':provenance}]
        elif entry['source']=='tenx' and platform=='Visium HD':
            from .tenx_online import ArchiveBudgetExceeded, ArchiveStream, collect
            urls=entry['catalog'].get('archives',{}).get('binned_outputs',[])
            if len(urls)!=1:
                rows=[{'anchor':entry['accession'],'status':'ambiguous_bundle' if urls else 'missing_files'}]
            else:
                cache=self.assets/'.archive-cache'
                digest=hashlib.sha256(urls[0].encode()).hexdigest()
                for root in self.roots:
                    matches=[p for p in root.rglob('.archive-cache') if (p/digest).is_dir()]
                    if matches:
                        cache=matches[0]
                        break
                stream=ArchiveStream(urls[0],2048*MIB,cache,max_network_bytes=remaining)
                try:
                    files=collect(stream,target)
                    mapping={role:{'path':str(item['path']),'sha256':sha256(item['path']),
                        'bytes':item['path'].stat().st_size,'member':item['member'],'source_url':urls[0]} for role,item in files.items()}
                    result={'files':mapping,'matrix_scope':'header_only',
                        'status':'files_prepared' if {'barcodes','matrix','positions','scalefactors','image'}<=mapping.keys() else 'missing_files'}
                except ArchiveBudgetExceeded as exc:
                    result={'status':'download_budget_exceeded','reason':str(exc)}
                finally:
                    self.usage[accession]=self.usage.get(accession,0)+stream.downloaded
                rows=[{'anchor':entry['accession']+'/square_002um',**result,
                    'bin_um':2,'downloaded_bytes':stream.downloaded,'reused_bytes':stream.reused,
                    'resolutions_not_processed_um':[8,16]}]
        elif entry['source']=='biostudies' and any(re.search(r'space_?ranger.*\.zip$',a['url'],re.I) for a in entry['assets']):
            raw = entry['accession']
            original = {'study_id':raw,'sample_id':raw,'platform':platform,'source':'BioStudies','found':True,
                        'requested_scope':{'organism':self.organism,'tissue':self.tissue}}
            destination = self.assets/raw/platform.replace(' ','_')
            result = validate_zip_study(self.client,original,entry['study'],entry['info'],destination,remaining,self.slices,True,
                prepare_only=True,anchor_filter=lambda anchor:self.anchor_condition(entry,anchor)['status'] in {'eligible','not_required'})
            self.usage[accession]=self.usage.get(accession,0)+max((r.get('transfer',{}).get('network_bytes_this_study',0) for r in result),default=0)
            for r in result:
                if 'sample_directory' not in r:
                    if self.request.disease or self.request.sample_state!='any':
                        rows.append({'anchor':raw,'status':'condition_unknown','condition_evidence':self.anchor_condition(entry,raw)})
                    else:
                        rows.append({'anchor':raw,'status':r['status']})
                    continue
                folder = destination/r['sample_id'].split('--')[1]
                files = {role:{**asset,'path':str(folder/Path(asset['member']).name)} for role,asset in r.get('files',{}).items()}
                rows.append({'anchor':r['sample_directory'],'status':r['status'],'files':files,
                             'condition_evidence':self.anchor_condition(entry,r['sample_directory']), 'transfer':r.get('transfer')})
        else:
            assets = [a for a in entry.get('assets',[]) if not re.search(r'\.(?:rds|pdf|fastq\.gz|bam)$',a['url'],re.I)]
            if entry['source']=='geo':
                scoped=[a for a in assets if '/'+entry['accession']+'/suppl/' in urlsplit(a['url']).path]
                if len(scoped)!=len(assets):
                    rows=[{'anchor':entry['accession'],'status':'ambiguous_asset_scope',
                           'reason':'Shared or cross-sample URLs require explicit sample/file mapping'}]
                assets=scoped
            if entry['source']=='biostudies' and (self.request.disease or self.request.sample_state!='any'):
                # Select only files with explicit metadata row -> published filename association.
                assets = [a for a in assets if self.anchor_condition(entry,unquote(urlsplit(a['url']).path.rsplit('/',1)[-1]))['status']=='eligible']
                if not assets:
                    rows = [{'anchor':entry['accession'],'status':'condition_unknown'}]
            if not rows:
                preparer = AssetPreparer(target,self.roots,remaining)
                prepared = preparer.prepare(assets,entry['accession'],platform)
                self.usage[accession]=self.usage.get(accession,0)+prepared['network_bytes']
                error_types={error['error_type'] for error in prepared['errors']}
                fallback=('download_budget_exceeded' if 'OverflowError' in error_types else
                    'ambiguous_bundle' if any('Ambiguous' in e['reason'] for e in prepared['errors']) else
                    'access_failed' if prepared['errors'] else 'missing_files')
                rows = prepared['groups'] or [{'anchor':entry['accession'],'status':fallback,'errors':prepared['errors']}]
                for row in rows:
                    row['asset_errors']=prepared['errors']
                    row['unsupported_assets']=prepared['unsupported_assets']
                entry['preparation_errors']=prepared['errors']
                entry['provenance']=prepared['provenance']
        prepared_tokens = set()
        for group in rows:
            token = accession + '--' + hashlib.sha256(group['anchor'].encode()).hexdigest()[:12]
            prepared_tokens.add(token)
            row = {**entry.get('row',{}),**group,'sample_id':token,'study_id':accession,'platform':platform,
                   'source':entry['source'],'found':True,'selected':True,'files_ready':group['status']=='files_prepared'}
            row.update(candidate_id=accession,study_id=entry.get('parent',accession),source_sample_id=entry['accession'])
            if entry['source']=='biostudies':
                row['source_sample_id']=group['anchor']
                row['clinical_status']=row.get('condition_evidence',{}).get('status',
                    'not_required' if not self.request.disease and self.request.sample_state=='any' else 'unknown')
            if group['status']=='condition_unknown':
                row.update(selected=False,clinical_status='unknown')
            previous = self.samples.get(token,{})
            def hashes(sample):
                return {role:asset.get('sha256') for role,asset in sample.get('files',{}).items()}
            if (previous.get('status') == 'correspondence_passed' and row['files_ready']
                    and hashes(previous) == hashes(row)):
                row = {**previous, **row, 'status':'correspondence_passed'}
            self.samples[token]=row
            self.groups[token]=group
        for token, row in self.samples.items():
            if (row.get('candidate_id') == accession and token not in prepared_tokens
                    and row['status'] == 'missing_or_changed_files'):
                row.update(status='missing_files',reason='The previous slice was absent from the replacement file groups')
        self.samples.pop(accession,None)
        entry['prepared']=all(r['status'] not in {'access_failed','processing_failed'} for r in rows)
        return {'samples':[{'sample_id':k,'status':r['status']} for k,r in self.samples.items() if r.get('candidate_id',r['study_id'])==accession],
                'retryable':not entry['prepared'],'network_bytes':self.usage.get(accession,0),
                'errors':entry.get('preparation_errors',[])}

    def validate_sample(self, sample_id, platform):
        row = self.samples.get(sample_id)
        if not row or row['platform']!=platform or row['status'] not in {'files_prepared','correspondence_passed','mismatch'}:
            raise ValueError('Validate only prepared samples from this session')
        target = self.out/'validation'/hashlib.sha256(sample_id.encode()).hexdigest()[:16]
        target.mkdir(parents=True,exist_ok=True)
        try:
            result=validate_group(row,platform,target)
        except (ValueError,KeyError) as exc:
            result={'status':'mismatch','reason':str(exc),'files_ready':True}
        checkpoint(target/'result.json',{**row,**result})
        row.update(result)
        if result['status'] == 'missing_or_changed_files':
            candidate = row.get('candidate_id',row['study_id'])
            self.entries[candidate]['prepared'] = False
            self.replayed.pop(operation_key('prepare_files',{'accession':candidate,'platform':platform}),None)
        return {k:row.get(k) for k in ('sample_id','status','checks','reason','cells','clinical_status')}

    def report(self):
        rows=list(self.samples.values())
        for token,entry in self.entries.items():
            if entry.get('expanded') or token in self.samples or any(r.get('candidate_id',r['study_id'])==token for r in rows):
                continue
            if entry.get('row'):
                rows.append(entry['row'])
        report={'samples':rows,'request':self.request.json(),'sources':self.sources,'source_modes':self.source_modes,'source_errors':self.source_errors,
                'requests':self.client.requests+self.geo.requests,'state':self.overview(),
                'read_limits':{k:len(e.get('not_read_due_to_limit',[])) for k,e in self.entries.items() if e.get('expanded')}}
        summarize(report)
        duplicates = annotate_catalog(rows,self.entries)
        report['verified_duplicates'] = duplicates
        report['verified_unique_count'] = report['totals']['passed']-sum(len(group['sample_ids'])-1 for group in duplicates)
        report['resume'] = {'count':self.resume_count,'invalidated_samples':self.invalidated,
                            'replayed_operations':sum(bool(e.get('reused_checkpoint')) for e in self.events)}
        searched={source+':'+platform for source in self.sources for platform in self.platforms}
        complete=(set(self.hits)==searched and all(e.get('reviewed') and (not e.get('selected') or e.get('prepared') or e.get('terminal_failure')) for e in self.entries.values())
                  and not any(r['status'] in {'files_prepared','metadata_selected','study_candidate','missing_or_changed_files'} for r in rows))
        report['workflow_complete']=complete
        if not complete:
            report['status']='incomplete'
        elif self.source_errors:
            report['status']='completed_with_issues'
        report['coverage_scope']='bounded candidate/sample limits; source snapshots and local archive identity limitations retained'
        return report
