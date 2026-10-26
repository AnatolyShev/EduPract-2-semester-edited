#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replicate the PCG Benchmark Arcade Rules experiment across independent sampling seeds.

Recommended (from a directory containing 2026 original output files):
 python run_arcade_replications.py --existing . --root replication_runs --seeds 2026 2027 2028 2029 --n 200

The original seed 2026 files are copied, never overwritten or reinterpreted as fresh runs.
New samples are simulated with the ORIGINAL official DoNothing/Random/FlatMCTS agent
protocol. Only one simulation is made per rule; baseline and hybrid share that trace.
The program checks hashes of Arcade Rules engine.py and problem.py against the original run.
No fake multi-seed statistics are created if only one seed is present.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, stdev

REQUIRED=('arcade_results.csv','arcade_summary.json','arcade_cases.json','arcade_proof_certificates.json')
PROVEN='PROVEN_UNREACHABLE_SCORE_WIN'
WITNESS='WITNESSED_SCORE_WIN'
UNKNOWN='INCONCLUSIVE'
VALID=(PROVEN,WITNESS,UNKNOWN)
EXPECTED_ENGINE='783f940700fa23b3e835c6e9195feca012abf428f540ee5c403ab6cf74e194d0'
EXPECTED_PROBLEM='90166c0873d9135a846be6a7e8f99d60ff677e2ff01f7b75da84728f2cdec3d0'


def check_run(folder:Path, expected_seed=None, expected_n=None):
    missing=[x for x in REQUIRED if not (folder/x).is_file()]
    if missing: raise ValueError(f'Missing files in {folder}: {missing}')
    with (folder/'arcade_results.csv').open('r',encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    summary=json.loads((folder/'arcade_summary.json').read_text(encoding='utf8'))
    cases=json.loads((folder/'arcade_cases.json').read_text(encoding='utf8'))
    certs=json.loads((folder/'arcade_proof_certificates.json').read_text(encoding='utf8'))
    if not rows or len(rows)!=summary['n'] or len(rows)!=len(cases):
        raise ValueError(f'Bad case count: {folder}')
    if expected_seed is not None and summary['seed']!=expected_seed: raise ValueError('Wrong seed')
    if expected_n is not None and len(rows)!=expected_n: raise ValueError('Wrong n')
    if summary.get('restriction_max_objects_per_color') is not None:
        raise ValueError('Restricted objects sample cannot be merged with unrestricted sample')
    if summary.get('engine_source_sha256')!=EXPECTED_ENGINE or summary.get('problem_source_sha256')!=EXPECTED_PROBLEM:
        raise ValueError(f'Engine/problem source hashes differ in {folder}; cannot pool incompatible game engines')
    ids=set(); digests=set(); counts={k:Counter() for k in ['baseline','coarse','hybrid']}; win=0
    for row in rows:
        i=row['id']; dig=row['sha256']
        if i in ids or dig in digests: raise ValueError(f'Duplicate id or content digest: {i}')
        ids.add(i); digests.add(dig)
        threshold=int(row['threshold']); coarse=int(row['u_naive']); resource=int(row['u_resource'])
        if resource>coarse: raise ValueError('Inverted bounds')
        w=any(row[f'{k}_win'].strip().lower() in ('true','1') for k in ('do_nothing','random','flat_mcts'))
        win+=w
        expected_baseline=WITNESS if w else UNKNOWN
        expected_coarse=PROVEN if coarse<threshold else expected_baseline
        expected_hybrid=PROVEN if resource<threshold else expected_baseline
        for k,expected in [('baseline',expected_baseline),('coarse',expected_coarse),('hybrid',expected_hybrid)]:
            observed=row[f'{k}_status']
            if observed != expected: raise ValueError(f'{i}: {k}={observed} instead of {expected}')
            counts[k][observed]+=1
    if len(certs)!=counts['hybrid'][PROVEN]: raise ValueError('Certificate count mismatch')
    caseids=set()
    for entry in cases:
        name=entry['id']
        digest=hashlib.sha256(json.dumps(entry['content'],sort_keys=True).encode('utf8')).hexdigest()
        target=next((r['sha256'] for r in rows if r['id']==name),None)
        if target!=digest: raise ValueError(f'Configuration hash mismatch for {name}')
        caseids.add(name)
    if caseids!=ids: raise ValueError('Case ids differ from CSV')
    for cert in certs:
        if cert['id'] not in ids: raise ValueError('Orphan certificate')
        record=next(x for x in rows if x['id']==cert['id'])
        if (cert['sha256']!=record['sha256'] or int(cert['u_resource'])!=int(record['u_resource']) or
            int(cert['u_naive'])!=int(record['u_naive']) or
            sum(int(p['bound']) for p in cert['pair_wise_certificate'])!=int(record['u_resource'])):
            raise ValueError(f'Certificate inconsistent: {cert["id"]}')
    for k in counts:
        if dict(counts[k])!=summary[k]:
            raise ValueError(f'CSV aggregate {k} does not match summary')
    def sec(col): return sum(float(r[col]) for r in rows if col in r and r[col])
    n=len(rows)
    sim=sec('engine_seconds'); quality=sec('quality_seconds'); static=sec('static_seconds')
    if abs(sim-summary['simulation_seconds_total'])>0.001 or abs(static-summary['additional_static_seconds_total'])>0.001:
        raise ValueError('Time sums inconsistent')
    det=counts['hybrid'][WITNESS]+counts['hybrid'][PROVEN]
    if any(x['baseline_status']==PROVEN for x in rows):raise ValueError('Baseline has a proof')
    return {'seed':int(summary['seed']),'n':n,'baseline_wins':counts['baseline'][WITNESS],
        'coarse_proofs':counts['coarse'][PROVEN],'hybrid_proofs':counts['hybrid'][PROVEN],
        'baseline_inconclusive':counts['baseline'][UNKNOWN], 'hybrid_inconclusive':counts['hybrid'][UNKNOWN],
        'baseline_decided_rate':counts['baseline'][WITNESS]/n,
        'hybrid_decided_rate':det/n,'gain_pp':100*counts['hybrid'][PROVEN]/n,
        'extra_proofs_vs_coarse':counts['hybrid'][PROVEN]-counts['coarse'][PROVEN],
        'simulation_wall_seconds':sim,'quality_wall_seconds':quality,'static_wall_seconds':static,
        'baseline_wall_seconds':sim+quality,'hybrid_wall_seconds':sim+quality+static,
        'overhead_pct':100*static/(sim+quality),
        'engine_sha256':summary['engine_source_sha256'],'problem_sha256':summary['problem_source_sha256'],
        'content_hashes':digests,
        'has_cpu_time_columns':'engine_cpu_seconds' in rows[0],
        'has_provenance_manifest':(folder/'pcg_environment_manifest.json').is_file()}


def persist_aggregate(root:Path, recs:list[dict]):
    root.mkdir(parents=True,exist_ok=True)
    recs=sorted(recs,key=lambda r:r['seed'])
    public=[{k:v for k,v in r.items() if k not in ('content_hashes','engine_sha256','problem_sha256')}
            for r in recs]
    keys=list(public[0])
    with (root/'replication_by_seed.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(public)
    unique=set();dups=0
    for r in recs:
        dups+=len(unique.intersection(r['content_hashes'])); unique.update(r['content_hashes'])
    k=len(recs)
    metrics=('baseline_decided_rate','hybrid_decided_rate','gain_pp','overhead_pct',
             'extra_proofs_vs_coarse','hybrid_proofs','baseline_wins')
    def stat(name):
        v=[r[name] for r in recs]
        return {'mean':mean(v),'sample_sd':stdev(v) if len(v)>1 else None,'min':min(v),'max':max(v)}
    aggregate={
      'completed_seeds':[r['seed'] for r in recs], 'completed_runs':k,
      'cases_total':sum(r['n'] for r in recs), 'distinct_rule_documents':len(unique),
      'overlap_count_across_seeds':dups,
      'metrics_per_run':{x:stat(x) for x in metrics},
      'pooled':{'baseline_wins':sum(r['baseline_wins'] for r in recs),
                'hybrid_proofs':sum(r['hybrid_proofs'] for r in recs),
                'extra_proofs_vs_coarse':sum(r['extra_proofs_vs_coarse'] for r in recs),
                'gain_pp':100*sum(r['hybrid_proofs'] for r in recs)/sum(r['n'] for r in recs)},
      'original_seed_2026':'Observed results; not generated from other seeds',
      'comparison':'Fixed agent traces: same rules/policies/simulation calls; static scoring adds measured wall time.',
      'cpu_time_warning':'Existing 2026 CSV contains perf_counter wall time only; v2 additionally records process_time CPU.',
      'precision_recall_warning':'Without independent ground truth: undecided is NOT a false result.',
      'no_fake_replications':'If only one completed run exists, sample_sd is null; no claims about seed robustness.',
    }
    (root/'replication_summary.json').write_text(json.dumps(aggregate,ensure_ascii=False,indent=2),encoding='utf8')
    def f(v):return 'не измерено' if v is None else f'{v:.3f}'
    out=['# Повторяемость Arcade Rules: результаты по seed','',
       'Каждая строка соответствует реально выполненной независимой выборке; для 2026 используются исходные пользовательские файлы.','',
       '| seed | n | Найдены победы | Доказано U1 < H | + к U0 | Доля определённых | Доп. wall time, с |',
       '|---:|---:|---:|---:|---:|---:|---:|']
    for r in recs:
        out.append(f"| {r['seed']} | {r['n']} | {r['baseline_wins']} | {r['hybrid_proofs']} | {r['extra_proofs_vs_coarse']} | {100*r['hybrid_decided_rate']:.1f}% | {r['static_wall_seconds']:.6f} |")
    out.extend(['',f'Выполнено независимых запусков: {k}. Уникальных JSON: {len(unique)}; повторов между запусками: {dups}.'])
    if k>1:
        v=aggregate['metrics_per_run']['gain_pp']
        out.append(f"Прирост доли определённых исходов по seed: {v['mean']:.2f} ± {v['sample_sd']:.2f} процентного пункта (среднее ± выборочное SD; n seed={k}).")
    else:
        out.append('Сейчас имеется только один seed: межзапусковая вариабельность НЕ ОЦЕНЕНА.')
    out.extend(['','Одинаковыми являются правила в парном сравнении, политики и число агентных запусков.',
                'Полное время не строго одинаково: гибрид добавляет статическую проверку; приведён дополнительный wall time.',
                'Время по perf_counter не следует называть процессорным (CPU) временем.',''])
    (root/'replication_report.md').write_text('\n'.join(out),encoding='utf8')
    print('\n'.join(out))
    return aggregate


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--existing',type=Path,default=Path('.'),help='Directory with original files from seed 2026')
    p.add_argument('--root',type=Path,default=Path('replication_runs'))
    p.add_argument('--seeds',type=int,nargs='+',default=[2026,2027,2028,2029])
    p.add_argument('--n',type=int,default=200)
    p.add_argument('--script',type=Path,default=Path(__file__).resolve().with_name('pcg_arcade_resource_experiment_v2.py'))
    p.add_argument('--no-run',action='store_true',help='Aggregate available runs only, never start missing runs')
    a=p.parse_args()
    if len(a.seeds)!=len(set(a.seeds)):p.error('Seeds must be unique')
    if 2026 not in a.seeds:p.error('Keep the original seed 2026 in this comparison')
    if a.n!=200:p.error('Original seed 2026 used n=200; use a matching n for all other runs')
    a.root.mkdir(parents=True,exist_ok=True)
    first=a.root/'seed_2026'
    if not first.exists():
        check_run(a.existing,expected_seed=2026,expected_n=a.n)
        first.mkdir(parents=True)
        for f in REQUIRED:shutil.copy2(a.existing/f,first/f)
        print(f'Archived original 2026 results to: {first}')
    recs=[]
    for seed in a.seeds:
        dest=a.root/f'seed_{seed}'
        if (dest/'arcade_results.csv').is_file():
            print(f'Reusing completed seed={seed}: {dest}')
        elif seed!=2026 and not a.no_run:
            # Fail before new experiments if installed engine differs from original run.
            from collect_pcg_environment_v2 import capture
            manifest=capture([a.script])
            if manifest['engine_source_sha256']!=EXPECTED_ENGINE or manifest['problem_source_sha256']!=EXPECTED_PROBLEM:
                raise SystemExit('STOP: installed Arcade Rules source does NOT match initial 2026 experiment. '
                                 f"engine: {manifest['engine_source_sha256']}; problem: {manifest['problem_source_sha256']}. "
                                 'Do not silently aggregate different engine versions.')
            dest.mkdir(parents=True,exist_ok=True)
            (dest/'pcg_environment_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
            command=[sys.executable,str(a.script.resolve()),'--n',str(a.n),'--seed',str(seed),'--output',str(dest.resolve())]
            print('Executing:', ' '.join(command),flush=True)
            subprocess.run(command,check=True)
        elif seed!=2026 and a.no_run:
            print(f'Not run yet: seed={seed}')
            continue
        recs.append(check_run(dest,expected_seed=seed,expected_n=a.n))
    if not recs:raise SystemExit('No runs available')
    persist_aggregate(a.root,recs)
    print(f'\nFiles: {a.root/"replication_by_seed.csv"}, {a.root/"replication_summary.json"}, {a.root/"replication_report.md"}')

if __name__=='__main__':main()
