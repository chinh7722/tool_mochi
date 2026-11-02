"""Authenticated service check without printing the token or account data."""
import argparse
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from core.remote.client import RemoteClient


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',default='')
    parser.add_argument('--url',default='http://127.0.0.1:8765')
    args=parser.parse_args()
    token=os.environ.get('HOSHINO_API_TOKEN','')
    if args.env_file:
        for line in Path(args.env_file).read_text(encoding='utf-8').splitlines():
            if line.startswith('HOSHINO_API_TOKEN='):
                token=line.split('=',1)[1].strip()
    state=RemoteClient(args.url,token).state()['state']
    print(f"Ready: protocol={state['version']}, active={state['active_jobs']}, "
          f"queued={state['queued_jobs']}, scheduled={state['scheduled_jobs']}")


if __name__=='__main__':
    main()
