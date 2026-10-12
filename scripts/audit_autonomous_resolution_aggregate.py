import argparse
from pathlib import Path
from recommend_book.autonomous_resolution_aggregate import aggregate
from recommend_book.autonomous_resolution import canonical
from recommend_book.autonomous_resolution_audit import strict_json


def main():
    p=argparse.ArgumentParser()
    for key in ('original','recovery','role-audit','terminal-observation','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();print(canonical(aggregate(a.original,a.recovery,a.role_audit,strict_json(a.terminal_observation.read_bytes()),a.output)))


if __name__=='__main__':main()
