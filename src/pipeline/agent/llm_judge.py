from pathlib import Path
import json
from pipeline.agent.agents import JudgeAgent

def eval_with_judge(run_identifier: str, judge: JudgeAgent):
    # parents[2] moves up two directories
    path = Path(__file__).resolve().parents[3]
    run_dir = path / 'outputs' /f"{run_identifier}"
    evaluation = json.loads((run_dir / 'evaluation.json').read_text(encoding='utf-8'))
    schemas = json.loads((run_dir / 'schemas.json').read_text(encoding='utf-8'))

    llm_judge_result = []
    for query, (case, schema) in enumerate(zip(evaluation['cases'], schemas, strict=True)):
        result = judge.forward(
            question=case['question'],
            schema=schema,
            reference_query=case['reference_query'],
            generated_query=case['final_query']
        )
        llm_judge_result.append({"query": f"query-{query}", "case" : case, "result": result})
    analysis_result = judgement_analysis(llm_judge_result)
    (run_dir / 'judge_results.json').write_text(json.dumps(llm_judge_result, indent=2, ensure_ascii=False), encoding='utf-8')
    (run_dir / 'analysis_judge_results.json').write_text(json.dumps(analysis_result, indent=2, ensure_ascii=False), encoding='utf-8')
    
    return llm_judge_result, analysis_result


# Get statistics on eval_with_judge results
def judgement_analysis(judge_results: list[dict]):
    tp = tn = fp = fn = 0
    correct = incorrect = uncertain = 0

    for item in judge_results:
        verdict = item['result']['verdict']

        if verdict == 'uncertain':
            uncertain += 1
            continue

        if verdict == 'correct':
            correct += 1
        elif verdict == 'incorrect':
            incorrect += 1
        else:
            continue

        # Positive = pipeline accepted the query
        predicted = item['case']['stop_reason'] == 'critic_accepted'

        # LLM judge provides proxy ground truth
        actual = verdict == 'correct'

        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and actual:
            fn += 1
        else:
            tn += 1

    total = len(judge_results)
    judged = correct + incorrect

    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0

    return {
        'total_queries': total,
        'correct': correct,
        'incorrect': incorrect,
        'uncertain': uncertain,
        'query_correctness_rate': correct / total if total else 0,
        'accuracy_excluding_uncertain': correct / judged if judged else None,
        'critic_accuracy': (tp + tn) / judged if judged else 0,
        'critic_precision': precision,
        'critic_recall': recall,
        'critic_f1': 2 * precision * recall / (precision + recall) if precision + recall else 0,
        'TP': tp,
        'TN': tn,
        'FP': fp,
        'FN': fn
    }