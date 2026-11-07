"""Run blinded retrieval, then score against the separately frozen reference."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GSM = re.compile(r'(?:^|:)(GSM\d+)(?:$|--)')


def ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def sample_id(row):
    for field in ('source_sample_id', 'candidate_id', 'sample_id'):
        match = GSM.search(row.get(field) or '')
        if match:
            return match[1]
    return None


def usage_report(trace, parsed, complete):
    model_messages = [m for m in trace.get('messages', []) if m.get('type') == 'ai']
    usages = [m.get('usage') for m in model_messages]
    if parsed.get('parser_model'):
        usages.append(parsed.get('usage'))
    measured = [u for u in usages if u and 'input_tokens' in u and 'output_tokens' in u]
    inputs = sum(u['input_tokens'] for u in measured)
    outputs = sum(u['output_tokens'] for u in measured)
    return {'input_tokens_recorded':inputs,'output_tokens_recorded':outputs,
            'reported_usage_calls':len(measured),'recorded_model_responses':len(usages),
            'usage_complete':bool(complete and usages and len(usages)==len(measured)),
            'recorded_tokens_peak_no_cache_estimate_usd':round((inputs*0.30+outputs*1.20)/1_000_000,8) if measured else None,
            'pricing_source':'https://api-docs.deepseek.com/quick_start/pricing/'}


def grade(reference, report):
    rows = report.get('samples', [])
    outcomes = []
    reference_ids = {case['sample'] for case in reference}
    for case in reference:
        matches = [row for row in rows if sample_id(row)==case['sample']]
        accepted = any(row.get('selected') for row in matches)
        positive = case['eligible']
        classification = ('true_positive' if accepted else 'false_negative') if positive else (
            'false_positive' if accepted else 'true_negative')
        outcomes.append({'sample':case['sample'],'reference_eligible':positive,'discovered':bool(matches),
                         'selected':accepted,'classification':classification,
                         'states':sorted({row['status'] for row in matches}),
                         'clinical_states':sorted({row['clinical_status'] for row in matches if row.get('clinical_status')})})
    counts = {name:sum(row['classification']==name for row in outcomes)
              for name in ('true_positive','false_positive','true_negative','false_negative')}
    unknown = sorted({sample_id(row) for row in rows if row.get('selected') and sample_id(row) not in reference_ids} - {None})
    unkeyed = sum(row.get('selected',False) and sample_id(row) is None for row in rows)
    selected = {sample_id(row) for row in rows if row.get('selected')} - {None}
    ready = {sample_id(row) for row in rows if row.get('selected') and row.get('files_ready')} - {None}
    passed = {sample_id(row) for row in rows if row.get('status')=='correspondence_passed'} - {None}
    return {'reference_counts':counts,'reference_samples':outcomes,
            'precision_among_labelled_selected':ratio(counts['true_positive'],counts['true_positive']+counts['false_positive']),
            'recall_on_frozen_positives':ratio(counts['true_positive'],counts['true_positive']+counts['false_negative']),
            'selected_unlabelled_sample_ids':unknown,'unkeyed_selected_rows':unkeyed,
            'observed_selected_samples':len(selected),'observed_files_ready':len(ready),
            'observed_spatial_passed':len(passed),'readiness_among_observed_selected':ratio(len(ready),len(selected))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reference', type=Path, default=HERE/'reference.json')
    parser.add_argument('--parallel', type=int, choices=(1,2,3), default=1)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    body = args.reference.read_bytes()
    plan = json.loads(body)

    def execute(query):
        directory = out/query['id']
        start = time.monotonic()
        with (out/(query['id']+'.log')).open('w',encoding='utf8') as log:
            completed = subprocess.run([sys.executable,'-m','sragent_spatial','research-spatial','--task',query['task'],
                '--sources',plan['source'],'--model','deepseek-flash','--max-studies',str(plan['max_studies']),
                '--samples-per-study',str(plan['samples_per_study']),'--download-mib',str(plan['download_mib_per_candidate']),
                '--out',str(directory)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        elapsed = round(time.monotonic()-start,2)
        def read(name):
            path = directory/name
            return json.loads(path.read_text(encoding='utf8')) if path.is_file() else {}
        report, trace, parsed = read('batch.json'), read('trace.json'), read('parsed_request.json')
        complete = completed.returncode==0 and bool(report.get('workflow_complete'))
        result = {'query':query['id'],'task':query['task'],'elapsed_seconds':elapsed,
                  'exit_code':completed.returncode,'workflow_complete':complete,
                  'agent_error':report.get('agent',{}).get('error'),'source_errors':report.get('source_errors',[]),
                  'normalized_filters':parsed.get('normalized'),'totals':report.get('totals'),
                  'cost':usage_report(trace,parsed,complete),
                  'evaluation':grade([row for row in plan['cases'] if row['query']==query['id']],report)}
        result['selected_samples'] = [
            {'sample':sample_id(row),'source_url':row.get('source_url'),'status_at_run':row['status'],
             'available_roles':sorted(row.get('files',{})),'publications':row.get('lineage',{}).get('publications',[]),
             'asset_error_types':sorted({error['error_type'] for error in row.get('asset_errors',[])})}
            for row in report.get('samples',[]) if row.get('selected')
        ]
        if not complete:
            result['partial_observations'] = result.pop('evaluation')
        (out/(query['id']+'.json')).write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
        print(query['id'], complete, elapsed, result.get('evaluation',{}).get('reference_counts'),flush=True)
        return result

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        results = list(pool.map(execute,plan['queries']))
    summary = {'model':'deepseek-flash','reference_sha256':hashlib.sha256(body).hexdigest(),
               'review_status':'source_curated_pending_independent_researcher',
               'reference_policy':plan['label_policy'],
               'scope':{'source':plan['source'],'max_studies':plan['max_studies'],
                        'samples_per_study':plan['samples_per_study'],
                        'download_mib_per_candidate':plan['download_mib_per_candidate']},
               'queries':results}
    if all(result['workflow_complete'] for result in results):
        costs = [result['cost']['recorded_tokens_peak_no_cache_estimate_usd'] for result in results]
        summary['aggregate'] = {
            'reference_counts':{key:sum(r['evaluation']['reference_counts'][key] for r in results)
                                for key in results[0]['evaluation']['reference_counts']},
            'sample_totals':{key:sum(r['totals'][key] for r in results) for key in ('found','selected','files_ready','passed')},
            'elapsed_seconds_summed':round(sum(r['elapsed_seconds'] for r in results),2),
            'estimated_usd_peak_uncached':round(sum(costs),8) if all(c is not None for c in costs) else None,
        }
    (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf8')


if __name__ == '__main__':
    main()
