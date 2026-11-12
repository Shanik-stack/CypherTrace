"""Offline end-to-end tests, including model boundary failures and persisted histories."""
from functools import partial
import json
from pathlib import Path
from runpy import run_path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from graph_query_analysis.input import InputValidationError
from pipeline.agent.critic_agent import decode_critique
from pipeline.agent.agents import CypherAgent
from pipeline.agent.orchestration import analyze_candidate, orchestrate_query
from pipeline.config import get_config
from pipeline.main import PROJECT_ROOT, main, run_pipeline
from pipeline.parser import build_request, normalize_generated_query, normalize_query
from test_python_semantics import SCHEMA, TRIALS


class ParserTests(unittest.TestCase):
    def test_fences_and_single_terminator(self):
        self.assertEqual(normalize_query('```cypher\nRETURN 1;\n```'), 'RETURN 1')
        self.assertEqual(normalize_query("RETURN ';' AS text;"), "RETURN ';' AS text")
        self.assertEqual(analyze_candidate('RETURN 1; RETURN 2', SCHEMA)['status'], 'parse_failed')

    def test_text_matches_normalized_ast_input(self):
        payload = build_request(' RETURN 1; ', SCHEMA, parameters={'p': 2}, request_id='chosen')
        self.assertEqual(payload['query_text'], 'RETURN 1')
        self.assertEqual(payload['parameters'], {'p': 2})
        self.assertEqual(payload['request_id'], 'chosen')

    def test_generated_query_strips_all_markdown_fences(self):
        query = 'MATCH (n)\nRETURN n'
        fenced = '```Cypher\nMATCH (n)\n```\n```cypher\nRETURN n ```'
        self.assertEqual(normalize_generated_query(fenced), query)
        self.assertEqual(analyze_candidate(normalize_generated_query(fenced), SCHEMA)['status'], 'compiled')

    def test_generated_fence_cleanup_preserves_valid_queries_and_rejects_distinct_queries(self):
        for valid in ('RETURN `name\n```', "RETURN 'line\n```\ntext'", 'RETURN 1 /*\n```\n*/'):
            with self.subTest(valid=valid):
                self.assertEqual(normalize_generated_query(valid), valid)
        for invalid in ('RETURN 1\n```RETURN 2', 'RETURN 1; RETURN 2\n```'):
            with self.subTest(invalid=invalid):
                cleaned = normalize_generated_query(invalid)
                self.assertNotIn('```', cleaned)
                self.assertEqual(analyze_candidate(cleaned, SCHEMA)['status'], 'parse_failed')

    def test_lexical_and_parse_errors_are_both_reported(self):
        for query in ('MATCH ( RETURN n', "RETURN 'unterminated", 'MATCH (a)<-[:R]->(b) RETURN b'):
            with self.subTest(query=query):
                self.assertEqual(analyze_candidate(query, SCHEMA)['status'], 'parse_failed')

    def test_ast_must_not_silently_drop_distinct(self):
        for query in ('RETURN count(DISTINCT *) AS total',
                      'RETURN coalesce(DISTINCT 1) AS value'):
            with self.subTest(query=query):
                self.assertEqual(analyze_candidate(query, SCHEMA)['status'], 'parse_failed')
        self.assertEqual(analyze_candidate('MATCH (n) RETURN count(DISTINCT n) AS total',
                                           SCHEMA)['status'], 'compiled')

    def test_unsupported_is_not_reported_as_success(self):
        self.assertEqual(analyze_candidate('OPTIONAL MATCH (n) RETURN n', SCHEMA)['status'], 'compilation_failed')


class OrchestrationTests(unittest.TestCase):
    def test_generated_fence_and_exact_duplicate_are_recovered_before_selection(self):
        query = TRIALS[2]
        duplicate = f'{query}\n```{query}'
        generator = Mock()
        generator.generate.side_effect = [f'{query}\n```', duplicate]
        result = orchestrate_query('Count', SCHEMA, generator=generator,
                                   max_revisions=1, improve_without_critic=True)
        self.assertEqual(result['final_query'], query)
        self.assertEqual(result['status'], 'compiled')
        self.assertEqual(result['stop_reason'], 'repeated_query')
        self.assertEqual(result['attempts'][0]['request']['query_text'], query)
        self.assertEqual(result['attempts'][0]['revision_response'], duplicate)

    def test_reference_needs_no_model(self):
        result = orchestrate_query('Count', SCHEMA, initial_query=TRIALS[0])
        self.assertEqual(result['status'], 'compiled')
        self.assertEqual(result['stop_reason'], 'compiled_without_critic')
        json.dumps(result)

    def test_revision_receives_prior_query_and_feedback(self):
        generator = Mock()
        generator.generate.return_value = TRIALS[2]
        critic = Mock()
        critic.review.side_effect = [{'needs_revision': True, 'feedback': 'Deduplicate accounts before AVG'},
                                     {'needs_revision': False, 'feedback': ''}]
        result = orchestrate_query('Count', SCHEMA, initial_query=TRIALS[0], generator=generator,
                           critic=critic, max_revisions=2)
        self.assertEqual(result['final_query'], TRIALS[2])
        self.assertEqual(result['selected_trial'], 2)
        self.assertEqual(result['stop_reason'], 'critic_accepted')
        self.assertIn(TRIALS[0], generator.generate.call_args.args[2])
        self.assertIn('Deduplicate accounts before AVG', generator.generate.call_args.args[2])
        self.assertEqual(generator.generate.call_count, 1)

    def test_bad_revision_keeps_last_compiled_candidate(self):
        generator = Mock()
        generator.generate.return_value = 'MATCH ( RETURN'
        result = orchestrate_query('Count', SCHEMA, initial_query=TRIALS[0], generator=generator,
                           max_revisions=1, improve_without_critic=True)
        self.assertEqual(result['final_query'], TRIALS[0])
        self.assertEqual(result['selected_trial'], 1)
        self.assertEqual(result['attempts'][-1]['status'], 'parse_failed')

    def test_provider_failure_propagates_in_generation_and_revision(self):
        generator = Mock()
        generator.generate.side_effect = RuntimeError('provider unavailable')
        for initial_query in (None, TRIALS[0]):
            with self.subTest(initial_query=initial_query), self.assertRaisesRegex(RuntimeError, 'unavailable'):
                orchestrate_query('Count', SCHEMA, initial_query=initial_query, generator=generator,
                                  max_revisions=1, improve_without_critic=True)

    def test_query_errors_are_sent_to_the_generator(self):
        for query, status in (('', 'parse_failed'), ('OPTIONAL MATCH (n) RETURN n', 'compilation_failed'),
                              ('RETURN 1 UNION RETURN 2', 'compilation_failed')):
            with self.subTest(query=query):
                generator = Mock()
                generator.generate.side_effect = [query, TRIALS[0]]
                result = orchestrate_query('Count', SCHEMA, generator=generator, max_revisions=1)
                first = result['attempts'][0]
                self.assertEqual(first['status'], status)
                self.assertIn(first['errors'][0], generator.generate.call_args.args[2])
                self.assertEqual(result['status'], 'compiled')

    def test_repeated_and_cyclic_queries_stop(self):
        generator = Mock()
        generator.generate.side_effect = [TRIALS[1], TRIALS[0]]
        result = orchestrate_query('Count', SCHEMA, initial_query=TRIALS[0], generator=generator,
                           max_revisions=5, improve_without_critic=True)
        self.assertEqual(result['stop_reason'], 'repeated_query')
        self.assertEqual(len(result['attempts']), 2)

    def test_critic_failure_propagates_without_revision(self):
        generator = Mock()
        critic = Mock()
        critic.review.side_effect = ValueError('Invalid critic response')
        with self.assertRaisesRegex(ValueError, 'Invalid critic response'):
            orchestrate_query('Count', SCHEMA, initial_query=TRIALS[0], generator=generator,
                              critic=critic, max_revisions=2)
        generator.generate.assert_not_called()

    def test_unused_generator_is_not_required(self):
        result = orchestrate_query('Count', SCHEMA, initial_query=TRIALS[0], max_revisions=2)
        self.assertEqual(result['status'], 'compiled')
        self.assertEqual(result['stop_reason'], 'compiled_without_critic')

    def test_analyzer_bug_is_not_misreported_as_model_error(self):
        with self.assertRaises(RuntimeError):
            orchestrate_query('Count', SCHEMA, initial_query=TRIALS[0],
                              analyzer=Mock(side_effect=RuntimeError('bug')))

    def test_report_only_revision_analyzes_each_new_query(self):
        generator = Mock()
        generator.generate.return_value = TRIALS[0]
        result = orchestrate_query('Count', SCHEMA, initial_query=TRIALS[2], generator=generator,
                                   max_revisions=1, improve_without_critic=True,
                                   max_report_chars=100000)
        self.assertEqual(result['stop_reason'], 'revision_limit')
        self.assertEqual(len(result['attempts']), 2)
        self.assertEqual(result['attempts'][0]['revision_feedback'], result['attempts'][0]['report'])
        self.assertNotEqual(result['attempts'][0]['report'], result['attempts'][1]['report'])
        self.assertIn(result['attempts'][0]['report'], generator.generate.call_args.args[2])


class PipelineTests(unittest.TestCase):
    def test_evaluation_config_runs_only_selected_case_with_bounded_generation(self):
        record = {'question': 'How many accounts?', 'schema': SCHEMA, 'cypher': TRIALS[2]}
        load = Mock(return_value=[record, {'schema': 'invalid'}])
        config = get_config('mtq_eval')
        config.update(limit=1, max_revisions=1)
        config['llm'].update(model='test', max_output_tokens=512)
        client = Mock()
        client.with_options.return_value = client
        usage = SimpleNamespace(input_tokens=100, output_tokens=30, total_tokens=130)
        client.responses.create.side_effect = [
            SimpleNamespace(output_text=query, status='completed', usage=usage)
            for query in (TRIALS[0], TRIALS[2])
        ]
        with (TemporaryDirectory() as folder,
              patch.dict(sys.modules, {'datasets': SimpleNamespace(load_dataset=load)}),
              patch('pipeline.config.get_config', return_value=config),
              patch('pipeline.agent.cypher_agent.CypherAgent',
                    side_effect=lambda **options: CypherAgent(client=client, **options)),
              patch('builtins.print') as console):
            config['output_dir'] = folder
            result = run_path(str(PROJECT_ROOT / 'src/pipeline/main.py'), run_name='__main__')['result']
            evaluation = result['evaluation']
            saved = Path(result['run_dir']) / 'evaluation.json'
            self.assertEqual(json.loads(saved.read_text(encoding='utf-8')), evaluation)
        load.assert_called_once_with('endeavorXx/Mind-the-Query', split='test', name='manual')
        client.with_options.assert_called_once_with(max_retries=0, timeout=45)
        self.assertEqual(evaluation['limit'], 1)
        self.assertEqual(evaluation['evaluated_rows'], 1)
        self.assertEqual(evaluation['model_requests'], 2)
        self.assertEqual(evaluation['token_usage_by_request'][1]['total_tokens'], 130)
        self.assertFalse(evaluation['cases'][0]['initial_exact_match'])
        self.assertTrue(evaluation['cases'][0]['final_exact_match'])
        self.assertEqual(evaluation['metrics']['initial_exact_match_accuracy'], 0.0)
        self.assertEqual(evaluation['metrics']['final_exact_match_accuracy'], 1.0)
        console.assert_any_call('Exact match accuracy: 0.0% initial, 100.0% final (1/1 references)')
        for call in client.responses.create.call_args_list:
            self.assertEqual(call.kwargs['max_output_tokens'], 512)
            self.assertNotIn(record['cypher'], call.kwargs['input'])
            self.assertNotIn(record['cypher'], call.kwargs['instructions'])

    def test_dataset_run_scores_revisions_and_counts_failed_queries_as_a_miss(self):
        records = [
            {'question': 'Gained', 'schema': SCHEMA, 'cypher': ' RETURN 2; '},
            {'question': 'Lost', 'schema': SCHEMA, 'cypher': 'RETURN 3'},
            {'question': 'Failed', 'schema': SCHEMA, 'cypher': 'RETURN 5'},
            {'question': 'No reference', 'schema': SCHEMA, 'cypher': ' '},
        ]
        generator = Mock()
        generator.generate.side_effect = [
            'RETURN 1', 'RETURN 2', 'RETURN 3', 'RETURN 4',
            'MATCH (', 'RETURN )', 'RETURN 6', 'RETURN 6',
        ]
        load_dataset = Mock(return_value=records)
        with (TemporaryDirectory() as folder,
              patch.dict(sys.modules, {'datasets': SimpleNamespace(load_dataset=load_dataset)})):
            run = main(split='test', limit='total', query_source='agent', generator=generator,
                       max_revisions=1, improve_without_critic=True, output_dir=folder)
            evaluation = run['evaluation']
            saved = Path(run['run_dir']) / 'evaluation.json'
            self.assertEqual(json.loads(saved.read_text(encoding='utf-8')), evaluation)

        metrics = evaluation['metrics']
        self.assertEqual(metrics['reference_count'], 3)
        self.assertEqual(metrics['initial_exact_match_accuracy'], 1 / 3)
        self.assertEqual(metrics['final_exact_match_accuracy'], 1 / 3)
        self.assertEqual(metrics['initial_compile_rate'], 3 / 4)
        self.assertEqual(metrics['final_compile_rate'], 3 / 4)
        self.assertEqual(metrics['exact_matches_gained'], 1)
        self.assertEqual(metrics['exact_matches_lost'], 1)
        self.assertFalse(evaluation['cases'][2]['final_exact_match'])
        self.assertIsNone(evaluation['cases'][3]['final_exact_match'])

    def test_offline_config_runs_without_a_model(self):
        config = get_config('offline')
        records = json.loads((PROJECT_ROOT / config.pop('records_file')).read_text(encoding='utf-8'))
        with TemporaryDirectory() as folder:
            result = run_pipeline(records, output_dir=folder, **config)
        self.assertEqual(result['compiled_count'], 3)
        self.assertEqual(get_config('mtq_agent')['max_revisions'], 2)

    def test_configured_model_options_reach_critic_and_revision_requests(self):
        config = get_config('default')
        config['improve_without_critic'] = False
        config['llm'].update(service='open_ai', model='test-model',
                             max_output_tokens=1234, reasoning_effort='low')
        records = json.loads((PROJECT_ROOT / config['records_file']).read_text(encoding='utf-8'))
        client = Mock()
        def response(text):
            return SimpleNamespace(output_text=text, status='completed',
                                   incomplete_details=None, error=None, usage=None)
        client.responses.create.side_effect = [
            response('{"needs_revision": true, "feedback": "Deduplicate b before AVG"}'),
            response(TRIALS[2]),
            response('{"needs_revision": false, "feedback": ""}'),
        ]
        with TemporaryDirectory() as folder:
            records_file = Path(folder) / 'records.json'
            records_file.write_text(json.dumps(records[:1]), encoding='utf-8')
            config.update(records_file=str(records_file), output_dir=folder)
            with (patch('pipeline.config.get_config', return_value=config),
                  patch('pipeline.agent.cypher_agent.CypherAgent',
                        side_effect=lambda **options: CypherAgent(client=client, **options)) as create_agent,
                  patch('builtins.print')):
                result = run_path(str(PROJECT_ROOT / 'src/pipeline/main.py'), run_name='__main__')['result']
        create_agent.assert_called_once_with(service='open_ai', model='test-model',
                                             max_output_tokens=1234, reasoning_effort='low')
        self.assertEqual(result['results'][0]['selected_trial'], 2)
        self.assertEqual(result['results'][0]['stop_reason'], 'critic_accepted')
        self.assertEqual(client.responses.create.call_count, 3)
        for call in client.responses.create.call_args_list:
            self.assertEqual(call.kwargs['model'], 'test-model')
            self.assertEqual(call.kwargs['max_output_tokens'], 1234)
            self.assertEqual(call.kwargs['reasoning'], {'effort': 'low'})

    def test_default_config_feeds_each_report_to_generator(self):
        config = get_config('default')
        self.assertTrue(config['improve_without_critic'])
        config.pop('llm')
        records = json.loads((PROJECT_ROOT / config.pop('records_file')).read_text(encoding='utf-8'))
        generator = Mock()
        generator.generate.side_effect = [TRIALS[1], TRIALS[2]]
        with TemporaryDirectory() as folder:
            result = run_pipeline(records[:1], output_dir=folder, generator=generator, **config)
            root = Path(result['run_dir']) / 'query_0000'
            for trial in range(1, 4):
                self.assertTrue((root / f'trial_{trial:02d}/report.txt').is_file())
        self.assertEqual(result['results'][0]['stop_reason'], 'revision_limit')
        self.assertEqual(len(result['results'][0]['attempts']), 3)
        self.assertEqual(generator.generate.call_count, 2)
        self.assertEqual([a['revision_source'] for a in result['results'][0]['attempts'][:2]],
                         ['symbolic_report', 'symbolic_report'])
        for attempt, call in zip(result['results'][0]['attempts'],
                                 generator.generate.call_args_list):
            self.assertIn(attempt['revision_feedback'], call.args[2])

    def test_same_agent_generates_and_reviews_query(self):
        client = Mock()
        response = lambda text: SimpleNamespace(output_text=text, status='completed',
                                                 incomplete_details=None, error=None, usage=None)
        client.responses.create.side_effect = [
            response(TRIALS[2]),
            response('{"needs_revision": false, "feedback": ""}'),
        ]
        agent = CypherAgent(client=client, model='test-model')
        records = [{'question': 'Count accounts', 'schema': SCHEMA}]
        with TemporaryDirectory() as folder:
            result = run_pipeline(records, query_source='agent', generator=agent, critic=agent,
                                  output_dir=folder, max_revisions=2)
            self.assertEqual(result['compiled_count'], 1)
            self.assertEqual(result['results'][0]['stop_reason'], 'critic_accepted')
            self.assertTrue((Path(result['run_dir']) / 'query_0000/trial_01/request.json').is_file())
        self.assertEqual(client.responses.create.call_count, 2)

    def test_batch_saves_query_diagnostics_indices_and_parameters(self):
        records = [{'question': 'q', 'schema': SCHEMA, 'cypher': q, 'parameters': {'x': 3}}
                   for q in ('RETURN $x', 'MATCH ( RETURN n', 'OPTIONAL MATCH (n) RETURN n', 'RETURN 4')]
        with TemporaryDirectory() as folder:
            output = run_pipeline(records, output_dir=folder, max_revisions=0, record_indices=[40, 40, 52, 61])
            root = Path(output['run_dir'])
            summaries = json.loads((root / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual([s['source_index'] for s in summaries], [40, 40, 52, 61])
            self.assertEqual([s['status'] for s in summaries],
                             ['compiled', 'parse_failed', 'compilation_failed', 'compiled'])
            payload = json.loads((root / 'query_0000/trial_01/request.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['parameters'], {'x': 3})
            self.assertEqual(payload['query_text'], 'RETURN $x')
            self.assertEqual(output['compiled_count'], 2)
            self.assertTrue(json.loads((root / 'errors.json').read_text(encoding='utf-8')))

    def test_invalid_schema_stops_the_batch(self):
        for schema, error in (('not json', json.JSONDecodeError), ({}, InputValidationError)):
            with self.subTest(schema=schema), TemporaryDirectory() as folder:
                records = [{'question': 'bad schema', 'cypher': 'RETURN 1', 'schema': schema}]
                with self.assertRaises(error):
                    run_pipeline(records, output_dir=folder)

    def test_runs_never_overwrite_previous_outputs(self):
        records = [{'question': 'q', 'schema': SCHEMA, 'cypher': 'RETURN 1'}]
        with TemporaryDirectory() as folder:
            first = run_pipeline(records, output_dir=folder)
            second = run_pipeline(records, output_dir=folder)
            self.assertNotEqual(first['run_dir'], second['run_dir'])
            self.assertTrue((Path(first['run_dir']) / 'summary.json').is_file())

    def test_saved_history_links_every_trial(self):
        records = [{'question': 'q', 'schema': SCHEMA, 'cypher': TRIALS[0]}]
        with TemporaryDirectory() as folder:
            generator = Mock()
            generator.generate.return_value = TRIALS[2]
            output = run_pipeline(records, output_dir=folder, generator=generator,
                                  max_revisions=1, improve_without_critic=True)
            root = Path(output['run_dir']) / 'query_0000'
            history = json.loads((root / 'history.json').read_text(encoding='utf-8'))
            self.assertEqual(len(history['attempts']), 2)
            for attempt in history['attempts']:
                self.assertTrue((root / attempt['report_file']).is_file())
                self.assertTrue((root / attempt['request_file']).is_file())
            self.assertEqual(history['selected_trial'], 2)


class ModelBoundaryTests(unittest.TestCase):
    def response(self, text, status='completed'):
        return SimpleNamespace(output_text=text, status=status, incomplete_details=None, error=None, usage=None)

    def test_completion_reuses_client_and_uses_sdk_output(self):
        client = Mock()
        client.responses.create.return_value = self.response('RETURN 1')
        agent = CypherAgent(client=client, model='test-model')
        self.assertEqual(agent.generate('q', SCHEMA), 'RETURN 1')
        self.assertEqual(agent.ask('instructions', 'input'), 'RETURN 1')
        self.assertEqual(client.responses.create.call_count, 2)

    def test_partial_visible_text_is_rejected(self):
        for text, status in [('', 'completed'), ('RETURN', 'incomplete'), ('RETURN 1', 'failed')]:
            with self.subTest(text=text, status=status):
                client = Mock()
                client.responses.create.return_value = self.response(text, status)
                with self.assertRaises(RuntimeError):
                    CypherAgent(client=client, model='test').ask('a', 'b')

    def test_critic_contract(self):
        self.assertEqual(decode_critique('```json\n{"needs_revision": false, "feedback": ""}\n```'),
                         {'needs_revision': False, 'feedback': ''})
        for text in ('[]', '{"needs_revision": "false", "feedback": ""}',
                     '{"needs_revision": true, "feedback": "  "}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                decode_critique(text)
        client = Mock()
        client.responses.create.return_value = self.response('{"needs_revision": false, "feedback": ""}')
        agent = CypherAgent(client=client, model='test')
        self.assertFalse(agent.review('q', SCHEMA, 'RETURN 1', 'report')['needs_revision'])


if __name__ == '__main__':
    unittest.main()
