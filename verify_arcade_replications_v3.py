#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recompute and validate 4 Arcade Rules replications from saved JSON/CSV.

No PCG Benchmark installation required. This audit reconstructs the standard
7x7 Arcade Rules layout from the pinned problem.py version and independently
recomputes pair-wise optimistic bounds and all labels.

Usage:
    python verify_arcade_replications_v3.py --zip replication_runs.zip --manifest pcg_environment_manifest.json --output replication_audit_v3.json

Important: cert validity also depends on the semantics of the pinned engine.
This checker validates arithmetic, statuses, data identity and consistency,
not all possible runtime behaviors of the game.
"""
import argparse
import collections
import csv
import hashlib
import io
import json
import math
import statistics
import zipfile
from pathlib import Path

PAIRS = ('player-red','player-green','player-yellow','red-red','red-green','red-yellow',
         'green-green','green-yellow','yellow-yellow')
COLORS = ('red','green','yellow')
POINTS = (0,1,2,4)
H = {1:5,2:10,3:20}
PROVEN = 'PROVEN_UNREACHABLE_SCORE_WIN'
WITNESS = 'WITNESSED_SCORE_WIN'
UNKNOWN = 'INCONCLUSIVE'
SEEDS = (2026,2027,2028,2029)

def layout7():
    # Matches ArcadeRulesProblem.__init__ (width=height=7), git 657aec8...
    m = [[1]*7 for _ in range(7)]
    for x in range(math.ceil(.25*7), math.floor(7-.25*7)):
        m[math.floor(.25*7)][x] = 0
        m[math.ceil(7-.25*7-1)][x] = 0
    for x in range(math.floor(.3*7)):
        m[int(.5*7)][x] = 0
        m[int(.5*7)][7-x-1] = 0
    return m

def count_pieces(c, m):
    n = {'player': 1}
    for color in COLORS:
        s=c[color+'Start'];n[color] = sum(
            0 <= int(s['x'][i]) < 7 and 0 <= int(s['y'][i]) < 7 and
            m[int(s['y'][i])][int(s['x'][i])] == 1
            for i in range(int(s['num']))
        )
    return n

def bounds(c, m):
    n=count_pieces(c,m)
    coarse=resource=0;details=[]
    for pair in PAIRS:
        a,b=pair.split('-')
        p=(n[a]*n[b] if a!=b else n[a]*(n[a]-1)//2)
        reward=POINTS[int(c[pair]['score'])];action=int(c[pair]['action'])
        x=p*reward*40;y=p*reward*(40 if action==0 else 1)
        coarse+=x;resource+=y
        details.append({'pair':pair,'n_pairs':p,'reward':reward,'action':action,'naive':x,'bound':y})
    return n,coarse,resource,details

def readjson(z,p):return json.loads(z.read(p).decode('utf-8'))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--zip', default='replication_runs.zip')
    ap.add_argument('--manifest', default='pcg_environment_manifest.json')
    ap.add_argument('--output', default='replication_audit_v3.json')
    args=ap.parse_args()
    manifest=json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    commit=manifest['exact_git_commit']
    assert commit=='657aec8b3ad06b033bde4d88f02c3145bb7848fe', 'Unexpected source commit'
    assert manifest['installed_distribution_version']=='0.1.0'
    m=layout7();summary={};all_hashes=set();problems=[]
    with zipfile.ZipFile(args.zip) as z:
        for seed in SEEDS:
            root=f'replication_runs/seed_{seed}/'
            rows=list(csv.DictReader(io.StringIO(z.read(root+'arcade_results.csv').decode('utf-8-sig'))))
            cases=readjson(z,root+'arcade_cases.json')
            certs=readjson(z,root+'arcade_proof_certificates.json')
            recorded=readjson(z,root+'arcade_summary.json')
            if seed!=2026:
                if readjson(z,root+'pcg_environment_manifest.json')!=manifest: problems.append(f'{seed}: different environment manifest')
            if recorded['seed']!=seed or recorded['n']!=200 or len(rows)!=200 or len(cases)!=200:problems.append(f'{seed}: unexpected sample size or seed')
            if recorded['engine_source_sha256']!=manifest['engine_source_sha256'] or recorded['problem_source_sha256']!=manifest['problem_source_sha256']:
                problems.append(f'{seed}: source hash mismatch')
            cases_by_id={x['id']:x['content'] for x in cases};cert_by_id={x['id']:x for x in certs}
            counts=collections.Counter();local=set()
            for r in rows:
                i=r['id'];c=cases_by_id[i]
                sha=hashlib.sha256(json.dumps(c,sort_keys=True).encode('utf-8')).hexdigest()
                if sha!=r['sha256']: problems.append(f'{seed}/{i}: JSON hash mismatch')
                if sha in local or sha in all_hashes: problems.append(f'{seed}/{i}: duplicated configuration')
                local.add(sha)
                n,u0,u1,details=bounds(c,m)
                if u0!=int(r['u_naive']) or u1!=int(r['u_resource']):problems.append(f'{seed}/{i}: bound mismatch')
                if any(n[color]!=int(r['objects_'+color]) for color in COLORS):problems.append(f'{seed}/{i}: piece count mismatch')
                threshold=H[int(c['win'])]
                if int(r['threshold'])!=threshold:problems.append(f'{seed}/{i}: threshold mismatch')
                won=any(r[x+'_win'].lower()=='true' for x in ('do_nothing','random','flat_mcts'))
                base=WITNESS if won else UNKNOWN
                coarse=PROVEN if u0<threshold else base
                hybrid=PROVEN if u1<threshold else base
                if (base,coarse,hybrid)!=(r['baseline_status'],r['coarse_status'],r['hybrid_status']):
                    problems.append(f'{seed}/{i}: statuses mismatch')
                if won and u1<threshold:problems.append(f'{seed}/{i}: CONTRADICTION: win and proof')
                if (u1<threshold)!=(i in cert_by_id):problems.append(f'{seed}/{i}: certificate existence mismatch')
                if i in cert_by_id:
                    cert=cert_by_id[i]
                    if cert['sha256']!=sha or cert['counts']!=n or cert['pair_wise_certificate']!=details:
                        problems.append(f'{seed}/{i}: certificate content mismatch')
                counts['n']+=1;counts['wins']+=int(won);counts['coarse']+=int(u0<threshold)
                counts['proofs']+=int(u1<threshold);counts['extra']+=int(u1<threshold and not u0<threshold)
                counts['unknown']+=int(hybrid==UNKNOWN)
            all_hashes.update(local)
            if len(certs)!=counts['proofs']:problems.append(f'{seed}: certificate count mismatch')
            for variant,counts_pair in (('baseline',{'WITNESSED_SCORE_WIN':counts['wins'],'INCONCLUSIVE':200-counts['wins']}),
                   ('coarse',{'WITNESSED_SCORE_WIN':counts['wins'],'INCONCLUSIVE':200-counts['wins']-counts['coarse'],PROVEN:counts['coarse']}),
                   ('hybrid',{'WITNESSED_SCORE_WIN':counts['wins'],'INCONCLUSIVE':counts['unknown'],PROVEN:counts['proofs']})):
                if {k:v for k,v in counts_pair.items() if v}!=recorded[variant]:problems.append(f'{seed}: {variant} aggregate mismatch')
            wall={x:sum(float(r[x]) for r in rows) for x in ('engine_seconds','quality_seconds','static_seconds')}
            if abs(wall['engine_seconds']-recorded['simulation_seconds_total'])>.001 or abs(wall['static_seconds']-recorded['additional_static_seconds_total'])>.001:
                problems.append(f'{seed}: timing mismatch')
            summary[str(seed)]={**dict(counts),'gain_pp':counts['proofs']/2,'seconds':wall}
        provided_summary=readjson(z,'replication_runs/replication_summary.json')
        totals={key:sum(summary[str(s)][key] for s in SEEDS) for key in ('n','wins','coarse','proofs','extra','unknown')}
        agg=provided_summary['pooled']
        if agg['baseline_wins']!=totals['wins'] or agg['hybrid_proofs']!=totals['proofs'] or agg['extra_proofs_vs_coarse']!=totals['extra']:
            problems.append('overall aggregate mismatch')
    gain=[summary[str(s)]['gain_pp'] for s in SEEDS]
    aggregate={
        'counts':totals, 'unique_json':len(all_hashes),
        'baseline_decided_pct':100*totals['wins']/totals['n'],
        'hybrid_decided_pct':100*(totals['wins']+totals['proofs'])/totals['n'],
        'gain_pp_mean':statistics.mean(gain),'gain_pp_sample_sd':statistics.stdev(gain),
        'sim_wall_s':sum(summary[str(s)]['seconds']['engine_seconds'] for s in SEEDS),
        'quality_wall_s':sum(summary[str(s)]['seconds']['quality_seconds'] for s in SEEDS),
        'static_wall_s':sum(summary[str(s)]['seconds']['static_seconds'] for s in SEEDS),
    }
    aggregate['overhead_pct']=100*aggregate['static_wall_s']/(aggregate['sim_wall_s']+aggregate['quality_wall_s'])
    result={'verified':not problems,'commit':commit,'individual_seeds':summary,'aggregate':aggregate,'issues':problems}
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFIED' if not problems else 'ISSUES FOUND', 'seed_runs:',len(SEEDS),'rules:',totals['n'],'proofs:',totals['proofs'],'extra_vs_coarse:',totals['extra'],'gain_pp:',aggregate['gain_pp_mean'])
    for i in problems[:20]:print(' -',i)
    if problems:raise SystemExit(1)
if __name__=='__main__':main()
