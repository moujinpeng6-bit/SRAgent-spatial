"""Persistent research state, publication links and conservative duplicate evidence."""
import json
import re
from collections import defaultdict
from pathlib import Path

from .biostudies import nodes
from .research_assets import checked_url, sha256


def publications(fields):
    """Accept identifiers only from source fields explicitly tied to publications."""
    links = []
    for field, values in fields.items():
        if field.casefold() not in {'series_pubmed_id', 'doi', 'pubmed id', 'pmid', 'publication doi'}:
            continue
        for value in values if isinstance(values, list) else [values]:
            value = str(value).strip()
            doi = re.fullmatch(r'(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)', value, re.I)
            if doi and 'doi' in field.casefold():
                links.append({'id':doi[1].lower(),'url':'https://doi.org/'+doi[1],'field':field})
            elif value.isdecimal() and 'doi' not in field.casefold():
                links.append({'id':value,'url':'https://pubmed.ncbi.nlm.nih.gov/'+value+'/','field':field})
    return links


def biostudies_publications(study):
    links = []
    for node in nodes(study):
        kind = node.get('type', '').casefold()
        if kind in {'publication', 'article'}:
            links.extend(publications({item.get('name',''):item.get('value','')
                                       for item in node.get('attributes',[])}))
        if kind == 'study':
            for link in node.get('links', []):
                labels = ' '.join(a.get('value','') for a in link.get('attributes', []))
                if not re.search(r'\b(?:publication|preprint|manuscript|paper)\b', labels, re.I):
                    continue
                url = link.get('url','')
                preprint = re.fullmatch(
                    r'https://www\.(?:bio|med)rxiv\.org/content/(10\.1101/\d{4}\.\d{2}\.\d{2}\.\d+)(?:v\d+)?', url)
                if preprint:
                    links.append({'id':preprint[1],'url':url,'field':'BioStudies study publication link'})
                else:
                    links.extend(publications({'Publication DOI':url}))
    return links


def files_match(row):
    files = row.get('files')
    if not files:
        return False
    try:
        return all(sha256(Path(asset['path'])) == asset['sha256'] for asset in files.values())
    except (KeyError, OSError):
        return False


def declared_images(fields):
    """Record official image links explicitly labelled in sample metadata."""
    links = []
    for value in fields.get('Sample_characteristics_ch1', []):
        match = re.fullmatch(r'\s*((?:microscope|morphology|histology|tissue|CytAssist) image)\s*:\s*(\S+)\s*', value, re.I)
        if match:
            try:
                url = checked_url(match[2])
            except ValueError:
                continue
            links.append({'role':match[1],'source_url':url,'field':'Sample_characteristics_ch1'})
    return links


def file_identity(row):
    if row.get('matrix_scope') == 'header_only':
        return None
    files = row.get('files', {})
    position_role = 'cells' if row.get('platform') == 'Xenium' else 'positions'
    roles = ['image', position_role]
    if 'h5' in files:
        roles.append('h5')
    else:
        roles.extend(('barcodes','features','matrix'))
    if row.get('platform') != 'Xenium':
        roles.append('scalefactors')
    else:
        roles.append('experiment')
    if any(not files.get(role, {}).get('sha256') for role in roles):
        return None
    return tuple([row['platform'],row.get('bin_um'),row.get('matrix_scope')] +
                 [(role, files[role]['sha256']) for role in roles])


def annotate_catalog(rows, entries):
    groups = defaultdict(list)
    for row in rows:
        row.pop('canonical_sample_id',None)
        row['observed_sample_state'] = (row.get('condition_evidence') or {}).get('sample_state','unknown')
        candidate = entries.get(row.get('candidate_id', row['sample_id']), {})
        parent = entries.get(candidate.get('parent'), {})
        papers = candidate.get('publications') or parent.get('publications') or []
        if not papers and candidate.get('source') == 'biostudies':
            papers = biostudies_publications(candidate.get('study', {}))
        image = row.get('files', {}).get('image', {})
        image_source = {key:image[key] for key in ('sha256','source_url','member') if key in image}
        archive = image.get('archive_url') or (row.get('provenance') or {}).get('archive_url')
        if image and archive:
            image_source['archive_url'] = archive
        row['lineage'] = {'sample_id':row['sample_id'],'study_id':row['study_id'],
                          'source_sample_id':row.get('source_sample_id'),
                          'source_url':row.get('source_url'),'publications':papers,'image':image_source,
                          'declared_images':declared_images(candidate.get('fields', {}))}
        if row['status'] == 'correspondence_passed':
            row['availability'] = ('correspondence_checked_matrix_header' if row.get('matrix_scope')=='header_only'
                                   else 'correspondence_and_counts_checked')
            identity = file_identity(row)
            if identity:
                groups[identity].append(row)
        elif row.get('files_ready'):
            row['availability'] = 'files_ready_checks_not_passed'
        elif row.get('files'):
            row['availability'] = 'partial_files'
        else:
            row['availability'] = 'metadata_only'
    duplicates = []
    for candidates in groups.values():
        if len({row['source'] for row in candidates}) < 2:
            continue
        ids = sorted(row['sample_id'] for row in candidates)
        duplicates.append({'canonical_id':ids[0],'sample_ids':ids,
                           'evidence':'same verified image, coordinates, expression and mapping-file hashes'})
        for row in candidates:
            row['canonical_sample_id'] = ids[0]
    return duplicates


def operation_key(action, args):
    return action, json.dumps(args, sort_keys=True)
