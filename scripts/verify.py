"""Run offline regressions using a clean interpreter with platform dependencies."""
import os
from pathlib import Path
import subprocess
import sys
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0,str(ROOT/'scripts'))
    from prepare_repository import check_mirrors
    check_mirrors()
    failed = []
    total = 0
    environment = {**os.environ,'QT_QPA_PLATFORM':'offscreen'}
    for path in sorted((ROOT/'tests').glob('test_*.py')):
        result = subprocess.run([sys.executable,'-I',str(path)],cwd=ROOT,env=environment,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        output=result.stdout.decode('utf-8',errors='replace')
        match=re.search(r'Ran (\d+) tests?',output)
        count=int(match[1]) if match else 0
        total+=count
        print(f'{path.stem}: {count} tests, '+('PASS' if result.returncode==0 else 'FAIL'),flush=True)
        if result.returncode:
            failed.append(path.name)
            print(output[-10000:],flush=True)
    print(f'TOTAL: {total}; failed: {failed}',flush=True)
    if failed:
        print('Failed: '+', '.join(failed))
    raise SystemExit(bool(failed))


if __name__=='__main__':
    main()
