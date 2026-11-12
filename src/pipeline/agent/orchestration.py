
"""Generate, analyze, review, and revise Cypher queries."""

import logging

from cypherast.errors import CypherastError
from graph_query_analysis.input import parse_requests
from graph_query_analysis.report import generate_report
from pipeline.agent.agents import CypherAgent, CriticAgent
from pipeline.agent.prompts import revision_prompt
from pipeline.parser import build_request, normalize_generated_query, normalize_query


logger = logging.getLogger(__name__)

def analyze_candidate_cypher(query, schema):

    payload = build_request(query, schema)
    statement = payload['query_ast'][0]['args']['this']['type']
    if statement != 'Query':
        message = f'Unsupported statement: {statement}'
        return {'status': 'compilation_failed', 'report': message, 'model_report': message}

    # do query graph analysis
    request = parse_requests(payload)[0]

    # generate report
    report = generate_report(request)

    return {
        'status': report.status,
        'report': report.render(),
        'model_report': report.model_report,
        'symbolic_status': report.symbolic_status
    }

def orchestrate_query(question, schema, generator, critic, initial_query=None, with_critic=False,
                      max_revisions=0, input_index=None, source_index=None, max_report_chars=2500):
    query_label = 'Query'
    if input_index is not None:
        query_label = f'Query {input_index} (dataset row {source_index})'
    
    #get graph schema
    schema = schema
    #original call
    try:
        query = generator.forward(question, schema)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        logger.warning('%s, trial 1: initial generation failed: %s', query_label, error)
        return {'final_query': None, 'selected_trial': None, 'status': 'failed',
                'stop_reason': 'query_error', 'error': error, 'attempts': []}
    
    #keep track of all attempts
    attempts = []
    #seen to stop incase of repeated query
    seen = set()
    stop_reason = 'revision_limit'
    
    

    for number in range(1, max_revisions + 2):

        try:
            query = normalize_generated_query(query)
            if query in seen:
                stop_reason = 'repeated_query'
                break
            analysis = analyze_candidate_cypher(query, schema)
        except CypherastError as exc:
            if query in seen:
                stop_reason = 'repeated_query'
                break
            logger.warning('%s, trial %s: Cypher parse failed: %s', query_label, number, exc)
            message = f'Cypher parsing failed:\n{exc}'
            analysis = {'status': 'parse_failed', 'report': message, 'model_report': message}
        seen.add(query)

        query_report = analysis['model_report'][:max_report_chars]
        attempt = {'number': number, 'query': query, **analysis, 'model_report': query_report}
        attempts.append(attempt)
        feedback = analysis.get('model_report', analysis['report'])

        criticism = ''

        if analysis['status'] == 'compiled':
            

            critic_query = (
                f"Original question:\n{question}\n\n"
                f"Candidate Cypher:\n{query}"
            )
            try:
                """Comment these in and out for testing"""
                # Critic response with report
                # response = critic.forward(critic_query, schema, query_report=query_report, response_format={'type': 'json_object'})
                
                #critic response without report
                response = critic.forward(critic_query, schema, response_format={'type': 'json_object'})
                
                critique = critic.decode_critique(response)
                
                needs_revision = critique['needs_revision']
                criticism = critique['feedback']
                if type(needs_revision) is not bool or not isinstance(criticism, str): raise ValueError('Critic must return a boolean needs_revision and string feedback')
            except Exception as exc:
                criticism = ''
                attempt['critic_error'] = f'{type(exc).__name__}: {exc}'
                logger.warning('%s, trial %s: critic failed: %s', query_label, number, attempt['critic_error'])
            else:
                attempt['critique'] = critique
                if not needs_revision:
                    stop_reason = 'critic_accepted'
                    break
            
        if number > max_revisions: break

        user_query = (
            f"Original question:\n{question}\n\n"
            f"{revision_prompt.format(previous_query=query)}"
        )
        
        #revision call
        try:
            query = generator.forward(
                user_query,
                schema,
                query_report=feedback,
                criticism=criticism
            )
        except Exception as exc:
            attempt['revision_error'] = f'{type(exc).__name__}: {exc}'
            logger.warning('%s, trial %s: revision generation failed: %s',
                           query_label, number + 1, attempt['revision_error'])
            stop_reason = 'revision_failed'
            break
        
    #select the most recent compiled query
    selected = next(
        (a for a in reversed(attempts) if a['status'] == 'compiled'),
        attempts[-1]
    )

    return {
        'final_query': selected['query'],
        'selected_trial': selected['number'],
        'status': selected['status'],
        'stop_reason': stop_reason,
        'attempts': attempts
    }
