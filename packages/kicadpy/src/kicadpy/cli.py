"""One JSON request on stdin, one JSON result on stdout."""
import argparse
import json
import sys
from .engine import Engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['inspect', 'view', 'snapshot', 'apply', 'route', 'diff', 'check', 'commit', 'undo'])
    parser.add_argument('project')
    args = parser.parse_args()
    try:
        request = {} if args.operation in ('inspect', 'view', 'snapshot') else json.load(sys.stdin)
        result = getattr(Engine(args.project), args.operation)(**request)
        print(json.dumps({'ok': True, 'result': result}))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'kind': type(exc).__name__}))
        sys.exit(1)


if __name__ == '__main__':
    main()
