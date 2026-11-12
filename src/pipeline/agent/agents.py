
"""One reusable client for generating and reviewing Cypher queries."""
import json
import os
from openai import OpenAI
from pipeline.agent.prompts import generator_base_prompt, critic_base_prompt, JUDGE_PROMPT
from dotenv import load_dotenv

#making tool for cohere model as it doesnt have output format
CRITIQUE_TOOL = {
    'type': 'function',
    'function': {
        'name': 'submit_critique',
        'description': 'Submit one review decision for the candidate Cypher query.',
        'parameters': {
            'type': 'object',
            'properties': {
                'needs_revision': {'type': 'boolean'},
                'feedback': {'type': 'string'},
            },
            'required': ['needs_revision', 'feedback'],
            'additionalProperties': False,
        },
    },
}

load_dotenv()
class Agent:
    def __init__(self, service='open_router', model=None, max_output_tokens=4096, reasoning_effort=None):
        self.service = service
        if service == "open_router":
            self.client = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=os.environ.get("OPENROUTER_API_KEY"),)
            self.model = model or os.environ.get("OPENROUTER_MODEL")
        elif service == "open_ai":
            self.client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"),)
            self.model = model or os.environ.get("OPENAI_MODEL")
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.token_usage = []

        self.system_prompt = """"""

    def forward(self, user_query: str, schema, query_report = "", criticism = "", response_format=None):
        request = dict(
            model= self.model,
                messages=[
                    {"role": "system", "content": f"{self.system_prompt}\n\nGraph schema:\n{json.dumps(schema, ensure_ascii=False)}"},
                    {"role": "user", "content": f"{user_query}"
                     + (f"\n\nSymbolic report (evidence, not instructions):\n{query_report}" if query_report else "")
                     + (f"\n\nCritic feedback (issues to address):\n{criticism}" if criticism else "")},
                ],
            max_completion_tokens = self.max_output_tokens
        )
        if self.service == 'open_router' and self.reasoning_effort is not None:
            request['extra_body'] = {'reasoning': {'effort': self.reasoning_effort}}

        # North Mini Code supports function calls but cannot enforce JSON mode.
        use_critique_tool = (response_format is not None and self.service == 'open_router'
                             and self.model == 'cohere/north-mini-code:free')
        if use_critique_tool:
            request['tools'] = [CRITIQUE_TOOL]
            request['tool_choice'] = 'required'
            request['messages'][0]['content'] += '\nUse submit_critique if available. Otherwise return one JSON object.'
        elif response_format:
            request['response_format'] = response_format
        response = self.client.chat.completions.create(**request)
        if response.usage:
            self.token_usage.append({
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            })
        choice = response.choices[0]
        if choice.finish_reason == 'length':
            raise ValueError(f'{self.model} hit the completion limit '
                             f'(max_output_tokens={self.max_output_tokens}, finish_reason=length)')
        if use_critique_tool and choice.message.tool_calls:
            calls = choice.message.tool_calls
            if len(calls) != 1 or calls[0].type != 'function' or calls[0].function.name != 'submit_critique':
                raise ValueError(f'Expected one submit_critique call (finish_reason={choice.finish_reason})')
            return calls[0].function.arguments
        content = (choice.message.content or '').strip()
        if not content:
            raise ValueError(f'Model returned an empty response (finish_reason={choice.finish_reason}, '
                             f'refusal={choice.message.refusal})')
        return content


class CypherAgent(Agent):
    def __init__(self, service='open_router', model=None, max_output_tokens=4096, reasoning_effort=None):
        super().__init__(service=service, model=model, max_output_tokens=max_output_tokens, reasoning_effort=reasoning_effort)
        self.system_prompt = generator_base_prompt


class CriticAgent(Agent):
    def __init__(self, service='open_router', model=None, max_output_tokens=4096, reasoning_effort=None):
        super().__init__(service=service, model=model, max_output_tokens=max_output_tokens, reasoning_effort=reasoning_effort)
        
        self.system_prompt = critic_base_prompt

    def decode_critique(self, response):
        response = response.strip()
        return json.loads(response)

class JudgeAgent(Agent):
    def __init__(self, service='open_router', model=None, max_output_tokens=16000):
        super().__init__(service=service,model=model,max_output_tokens=max_output_tokens,reasoning_effort="high")
        self.system_prompt = JUDGE_PROMPT

    def forward(self, question, schema, reference_query, generated_query):
        prompt = f"""
            Original question:
            {question}

            Reference Cypher:
            {reference_query}

            Generated Cypher:
            {generated_query}
        """
        response = super().forward(
            prompt,
            schema,
            response_format={'type': 'json_object'}
        )
        return json.loads(response)
        
    