"""Review previously generated Cypher queries without generating new ones.

Save this file as src/critic_only_test.py and point run_dir to a saved run.
"""
import json
from pathlib import Path

from pipeline.agent.agents import CriticAgent
from pipeline.data.load_data import load_records


run_dir = Path(r"outputs")
model = 'baidu/ernie-5.5-flash-preview:free'
positions = [0, 1, 6, 9]  # Four useful cases to start with

cases = json.loads((run_dir / 'evaluation.json').read_text(encoding='utf-8'))['cases']
records = load_records(indices=[cases[i]['source_index'] for i in positions])
critic = CriticAgent(model=model)
results = []

for position, record in zip(positions, records):
    case = cases[position]
    history = json.loads((run_dir / f'query_{position:04d}' / 'history.json').read_text(encoding='utf-8'))
    attempt = next(a for a in history['attempts'] if a['number'] == history['selected_trial'])

    question = record['question']
    query = case['final_query']
    report = attempt.get('model_report', attempt['report'])

    critic_query = f'Original question:\n{question}\n\nCandidate Cypher:\n{query}'

    try:
        response = critic.forward(critic_query, record['schema'], query_report=report,
                                  response_format={'type': 'json_object'})
        critique = critic.decode_critique(response)
    except Exception as exc:
        critique = {'error': f'{type(exc).__name__}: {exc}'}

    results.append({'source_index': case['source_index'], 'model': model,
                    'query': query, 'critique': critique})
    print(f"Row {case['source_index']}: {critique}")

expected = {0: False, 1: True, 6: True, 9: True}  # Preliminary manual labels
correct = sum(r['critique'].get('needs_revision') == expected[r['source_index']] for r in results)
print(f'Critic decision agreement: {correct}/{len(results)}')

filename = f"critic_only_{model.replace('/', '_').replace(':', '_')}.json"
(run_dir / filename).write_text(
    json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8'
)
