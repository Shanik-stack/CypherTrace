
"""Run Cypher generation, analysis, and dataset evaluation."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from config import get_config
from pipeline.agent.agents import CypherAgent, CriticAgent, JudgeAgent
from pipeline.agent.orchestration import orchestrate_query
from pipeline.agent.llm_judge import eval_with_judge
from pipeline.data.load_data import load_records
from pipeline.parser import normalize_query
import os
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


"""
Defining record and result for reference.

record: Input from the Dataset.
result: Output from the Orchestrator

structure of record: 
[
    {
        "question": ...,
        "schema": ...,
        "cypher": ...,
        "parameters": ...
    }
]

structure of result:
[
    {
        "input_index": ..., The position of the record in the current input batch.
        "source_index": ...,The original position of the record in the dataset.
        "question": ...,
        "final_query": ...,
        "selected_trial": ..., The trial number whose Cypher query was selected as the final result.
        "status": ...,
        "stop_reason": ...,
        "attempts": [
            {
                "number": ...,
                "query": ...,
                "status": ...,
                "report": ...,
                "critique": ...
            }
        ]
    }
]

"""


def evaluate_results(records, results):
    cases = []

    for record, result in zip(records, results):
        reference = normalize_query(record.get('cypher') or '') or None
        initial = result['attempts'][0] if result['attempts'] else {}

        cases.append({
            'source_index': result['source_index'],
            'question': record['question'],
            'reference_query': reference,
            'initial_query': initial.get('query'),
            'initial_status': initial.get('status', result['status']),
            'final_query': result['final_query'],
            'final_status': result['status'],
            'initial_exact_match': initial.get('query') == reference if reference else None,
            'final_exact_match': result['final_query'] == reference if reference else None,
            'stop_reason': result['stop_reason']
        })

    references = [case for case in cases if case['reference_query'] is not None]
    initial_matches = sum(case['initial_exact_match'] for case in references)
    final_matches = sum(case['final_exact_match'] for case in references)
    initial_compiled = sum(case['initial_status'] == 'compiled' for case in cases)
    final_compiled = sum(case['final_status'] == 'compiled' for case in cases)

    metrics = {
        'reference_count': len(references),
        'initial_exact_matches': initial_matches,
        'final_exact_matches': final_matches,
        'initial_exact_match_accuracy': initial_matches / len(references) if references else None,
        'final_exact_match_accuracy': final_matches / len(references) if references else None,
        'exact_matches_gained': sum(case['final_exact_match'] and not case['initial_exact_match'] for case in references),
        'exact_matches_lost': sum(case['initial_exact_match'] and not case['final_exact_match'] for case in references),
        'initial_compiled': initial_compiled,
        'final_compiled': final_compiled,
        'initial_compile_rate': initial_compiled / len(cases) if cases else None,
        'final_compile_rate': final_compiled / len(cases) if cases else None
    }

    return {'metrics': metrics, 'cases': cases}


def run_pipeline(records, max_revisions=2, output_dir=None, record_indices=None,
                 agent_instructions=None, max_report_chars=1500, llm_as_judge = True):
    unique_file_identifier = datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S%fZ')
    output_dir = PROJECT_ROOT / 'outputs'
    run_dir = Path(output_dir) / unique_file_identifier
    run_dir.mkdir(parents=True)
    print(f"\n\n --------------- RUN: {run_dir} ------------------- \n\n")
    
    results = []
    if agent_instructions:
        generator= CypherAgent(**agent_instructions)
        critic= CriticAgent(**agent_instructions)
    else:
        generator= CypherAgent()
        critic= CriticAgent()
    
    for position, record in enumerate(records):
        source_index = record_indices[position] if record_indices is not None else position
        try:
            result = orchestrate_query(
                record['question'], record['schema'],
                generator=generator, critic=critic, max_revisions=max_revisions,
                input_index=position, source_index=source_index, max_report_chars=max_report_chars
            )
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            logger.warning('Query %s (dataset row %s) failed: %s', position, source_index, error)
            result = {'final_query': None, 'selected_trial': None, 'status': 'failed',
                      'stop_reason': 'query_error', 'error': error, 'attempts': []}
        result.update(
            input_index=position,
            source_index=source_index,
            question=record.get('question')
        )
        results.append(result)

        folder = run_dir / f'query_{position:04d}'
        folder.mkdir()

        for attempt in result['attempts']:
            trial = folder / f"trial_{attempt['number']}"
            trial.mkdir()
            (trial / 'query.cypher').write_text(attempt['query'], encoding='utf-8')
            (trial / 'report.txt').write_text(attempt['report'], encoding='utf-8')

        (folder / 'history.json').write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        summary = [{k: v for k, v in r.items() if k != 'attempts'} for r in results]
        (run_dir / 'summary.json').write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8'
        )

    evaluation = evaluate_results(records, results)
    evaluation['model'] = generator.model
    evaluation['max_revisions'] = max_revisions
    evaluation['max_report_chars'] = max_report_chars
    evaluation['token_usage_by_request'] = generator.token_usage
    if critic is not None:
        evaluation['critic_token_usage_by_request'] = critic.token_usage

    (run_dir / 'evaluation.json').write_text(
        json.dumps(evaluation, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    (run_dir / 'schemas.json').write_text(
        json.dumps([record['schema'] for record in records], indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    
    # RUN LLM AS JUDGE pipeline
    if llm_as_judge:
        Judge = JudgeAgent()
        judgement_results, analysis_judgement_results  = eval_with_judge(unique_file_identifier, Judge)
        

    return {
        'results': results,
        'evaluation': evaluation,
        'run_dir': str(run_dir),
        'attempted_queries': len(results),
        'compiled_count': evaluation['metrics']['final_compiled']
    }


def main(dataset_name='mtq', split='train', indices=None, limit=None, validation_type='manual',
         max_revisions=2, output_dir=None, agent_instructions=None, max_report_chars=1500, llm_as_judge = True):
    indices = list(indices) if indices is not None else None
    #get record from dataset
    records = load_records(dataset_name, split=split, indices=indices, limit=limit, validation_type=validation_type)
    #get orchestration output
    return run_pipeline(records, max_revisions=max_revisions, output_dir=output_dir,
                        record_indices=indices, agent_instructions=agent_instructions,
                        max_report_chars=max_report_chars, llm_as_judge = llm_as_judge)



if __name__ == "__main__":
    response = requests.get(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    )
    print("\n\n ----------------------------- OPENROUTER CREDITS LEFT -------------------- |\n\n")
    print(response.json())
    CONFIG_NAME = "mtq_eval"
    config  = get_config(CONFIG_NAME)
    dataset_name = config["dataset_name"]
    split = config["split"]
    limit = config["limit"]
    max_revisions = config["max_revisions"]
    agent_instructions = config["agent"]
    llm_as_judge = bool(config["llm_as_judge"])
    Judge = JudgeAgent()
    judgement_results, analysis_judgement_results  = eval_with_judge(run_identifier = "run-20261112T205017618408Z", judge = Judge)
    
    # output = main(dataset_name=dataset_name, split=split, limit=limit, max_revisions=max_revisions,
    #               agent_instructions=agent_instructions, max_report_chars=config.get('max_report_chars', 1500), llm_as_judge = llm_as_judge)
    # print(output)
