#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record reproducibility information for the installed PCG Benchmark.

Usage: python collect_pcg_environment_v2.py --output pcg_environment_manifest.json
This does not install anything and does not claim a Git commit when it is unknown.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata as md
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


def digest(file):
    return hashlib.sha256(Path(file).read_bytes()).hexdigest()


def git_info(path):
    try:
        base = Path(path).resolve()
        q = subprocess.run(['git','-C',str(base),'rev-parse','--show-toplevel'],capture_output=True,text=True,timeout=4)
        if q.returncode:
            return None
        root = Path(q.stdout.strip()).resolve()
        if not base.is_relative_to(root):
            return None
        # Do not mistake an enclosing user's project with a venv for the benchmark's Git checkout.
        if not (base.parent == root or (base.parent.name == 'src' and base.parent.parent == root)):
            return None
        commit = subprocess.run(['git','-C',str(base),'rev-parse','HEAD'],capture_output=True,text=True,timeout=4)
        dirty = subprocess.run(['git','-C',str(base),'status','--porcelain'],capture_output=True,text=True,timeout=4)
        return {'repository_root': str(root), 'commit':commit.stdout.strip() if commit.returncode==0 else None,
                'dirty':bool(dirty.stdout.strip()) if dirty.returncode==0 else None}
    except (OSError, subprocess.TimeoutExpired):
        return None


def capture(extra_files=None):
    import pcg_benchmark
    from pcg_benchmark.probs.arcaderules import engine, problem
    import numpy
    p = Path(pcg_benchmark.__file__).resolve().parent
    dist = md.distribution('pcg_benchmark')
    direct = dist.read_text('direct_url.json')
    install_source = None
    if direct:
        try:
            install_source = json.loads(direct)
        except json.JSONDecodeError:
            install_source = {'raw':direct}
    commit = (install_source or {}).get('vcs_info', {}).get('commit_id') if isinstance(install_source,dict) else None
    git = git_info(p)
    if not commit and git:
        commit = git.get('commit')
    version_deps = {}
    for k in ('numpy','Pillow','pandas','pcg_benchmark'):
        try: version_deps[k] = md.version(k)
        except md.PackageNotFoundError: version_deps[k] = None
    # Full file-hash inventory makes differences visible without inventing release tags.
    pkg_hashes = {str(f.relative_to(p)).replace('\\','/'):digest(f)
                  for f in sorted(p.rglob('*.py')) if f.is_file()}
    out = {
        'package_name':'pcg_benchmark', 'installed_distribution_version':md.version('pcg_benchmark'),
        'source':'installed local package', 'install_provenance':install_source,
        'exact_git_commit':commit, 'git_checkout':git,
        'git_commit_note':('Known from installed provenance or actual repository' if commit else
                           'UNKNOWN: pip show Version does not identify a commit; use hashes below'),
        'pcg_module_location':str(p),
        'engine_source_sha256':digest(engine.__file__), 'problem_source_sha256':digest(problem.__file__),
        'python_source_file_hashes':pkg_hashes,
        'python_source_count':len(pkg_hashes),
        'dependency_versions':version_deps,
        'python_version':sys.version, 'python_executable':sys.executable,
        'platform':platform.platform(), 'processor':platform.processor(),
        'machine':platform.machine(), 'logical_cpu_count':os.cpu_count(),
        'experiment_program_hashes':{Path(z).name:digest(z) for z in (extra_files or []) if Path(z).is_file()},
        'timing_convention':'wall_clock=time.perf_counter; process_cpu=time.process_time; these are not interchangeable',
    }
    return out


def main():
    a=argparse.ArgumentParser()
    a.add_argument('--output',type=Path,default=Path('pcg_environment_manifest.json'))
    a.add_argument('--script',type=Path,action='append',default=[])
    args=a.parse_args()
    try:
        env=capture(args.script)
    except ImportError as exc:
        raise SystemExit(f'Cannot import PCG Benchmark in this Python interpreter: {exc}. Use the same interpreter as the experiment.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(env,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Manifest:',args.output)
    print('Installed version:',env['installed_distribution_version'])
    print('Exact commit:',env['exact_git_commit'] or 'not available (source hashes captured)')
    print('Engine SHA256:',env['engine_source_sha256'])
    print('Problem SHA256:',env['problem_source_sha256'])
    print('CPU:',env['processor'] or 'unavailable: record manually')
if __name__=='__main__':main()
