"""CLI fallback and resumable authoring adapter; same backend/database as MCP."""
import argparse, json
from pathlib import Path
from orimem.core.backend import Backend

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action')
    parser.add_argument('--args', default='{}')
    parser.add_argument('--args-file', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    values = json.loads(args.args_file.read_text(encoding='utf-8') if args.args_file else args.args)
    result = Backend().lifecycle.dispatch(args.action, values)
    content = json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(content,encoding='utf-8')
        print(json.dumps(dict(output=str(args.output)),ensure_ascii=False))
    else:
        print(content)

if __name__ == '__main__':
    main()
