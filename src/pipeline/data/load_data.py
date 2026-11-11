
import json
from copy import deepcopy

from datasets import load_dataset
from huggingface_hub import hf_hub_download

from pipeline.data.neo4j_schema import Neo4jSchema


def normalize_schema_row(row):
    return {
        'schema': json.dumps(Neo4jSchema(row['schema']).to_dict(),sort_keys=True,)
    }


class MTQ_dataset:
    def __init__(self, validation_type='manual'):
        self.dataset = load_dataset('endeavorXx/Mind-the-Query', name=validation_type)

        # Normalize the schema column in every split once at load time.
        # Keep JSON strings so Hugging Face's Arrow column stays homogeneous.
        self.dataset = self.dataset.map(normalize_schema_row,)
        self.train_dataset = self.dataset['train']
        self.test_dataset = self.dataset['test']

    def __len__(self):
        return len(self.test_dataset)

    def __getitem__(self, index):
        row = dict(self.test_dataset[index])
        # Decode the already-normalized schema, without re-normalizing.
        row['schema'] = json.loads(row['schema'])
        return row

    def get_schemas(self, split='test', unique=False):
        items = self.dataset[split]['schema']
        if unique:
            items = list(dict.fromkeys(items))
        return [json.loads(text) for text in items]


class CypherBench_dataset:
    def __init__(self, repo='megagonlabs/cypherbench'):
        dataset = load_dataset(
            'json', data_files=f'hf://datasets/{repo}/test.json', split='train'
        )
        self.schemas = {}
        for graph in sorted(set(dataset['graph'])):
            schema_path = hf_hub_download(
                repo_id=repo,
                filename=f'graphs/schemas/{graph}_schema.json',
                repo_type='dataset',
            )
            with open(schema_path, encoding='utf-8') as f:
                self.schemas[graph] = Neo4jSchema(json.load(f)).to_dict()
        self.records = [{
            'question': row['nl_question'],
            'schema': self.schemas[row['graph']],
            'cypher': row['gold_cypher'],  
            'expected_result': row['answer_json'],
            'graph': row['graph'],
            'qid': row.get('qid'),
        } for row in dataset]

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        item = dict(self.records[index])
        item['schema'] = deepcopy(item['schema'])
        return item


if __name__ == '__main__':
    mtq = MTQ_dataset()
    cyb = CypherBench_dataset()
    for dataset in (mtq, cyb):
        item = dataset[0]
        print(item['question'])
        print(item['cypher'])
        print(json.dumps(item['schema'], indent=2))
