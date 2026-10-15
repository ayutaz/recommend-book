import argparse
from pathlib import Path
from recommend_book.autonomous_resolution_recovery import recover
from recommend_book.autonomous_resolution_audit import strict_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--original',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--role-audit',type=Path,required=True);p.add_argument('--original-terminal',type=Path,required=True);a=p.parse_args()
    recover(a.original,a.output,a.role_audit,strict_json(a.original_terminal.read_bytes()))


if __name__=='__main__':main()
