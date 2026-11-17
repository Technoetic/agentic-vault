"""Scripted participant for the synthetic handoff experiment. Run by its evaluator."""
import json
from pathlib import Path
import sys


def write(name, value):
    Path(name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def advance(state, entries):
    for entry in entries:
        if entry['accepted']:
            state['accepted_ids'].append(entry['id'])
            state['total'] += entry['units']
        state['next_index'] += 1


def main(stage):
    if stage == 'resume':
        state = json.loads(Path('handoff.json').read_text(encoding='utf-8'))
    else:
        request = json.load(sys.stdin)
        state = {'task': request['task'], 'source': request['source'],
                 'accepted_ids': [], 'total': 0, 'next_index': 0}
    if state['source'] not in ('a-r1.json', 'a-r2.json', 'b-r2.json'):
        raise ValueError('unknown_demo_snapshot')
    rows = json.loads(Path(state['source']).read_text(encoding='utf-8'))
    if stage != 'resume':
        advance(state, rows[:1])
    if stage == 'checkpoint':
        write('handoff.json', state)
        return
    if stage == 'interrupt':
        write('interrupt-checkpoint.json', state)
    advance(state, rows[state['next_index']:])
    del state['next_index']
    write('interrupt-result.json' if stage == 'interrupt' else 'focus-result.json', state)


if __name__ == '__main__':
    main(sys.argv[1])
