

import json
from pathlib import Path

from pipeline.data.preprocess import preprocess_cypherbench, preprocess_mtq
from datasets import load_dataset


def load_records(dataset_name='mtq', split='train', indices=None, limit=None,
                 validation_type='manual'):
    #get MindTheQuery
    if dataset_name == 'mtq':
        rows = load_dataset('endeavorXx/Mind-the-Query', name=validation_type, split=split)
    #get cyberbench
    else:
        from huggingface_hub import hf_hub_download
        rows = load_dataset('json', data_files='hf://datasets/megagonlabs/cypherbench/test.json', split='train')
    
    #define set of indices to evaluate on subset of dataset to avoid large llm calls
    if indices is None:
        count = len(rows) if limit == 'total' else (limit if limit is not None else 1)
        indices = range(count)

    selected = []
    schemas = {}

    for index in indices:
        row = dict(rows[index])
        
        #shcema present in mtq row
        if dataset_name == 'mtq':
            selected.append(preprocess_mtq(row))

        else:
            graph = row['graph']

            if graph not in schemas:
                path = hf_hub_download(
                    repo_id='megagonlabs/cypherbench',
                    filename=f'graphs/schemas/{graph}_schema.json',
                    repo_type='dataset'
                )
                schemas[graph] = json.loads(Path(path).read_text(encoding='utf-8'))

            selected.append(preprocess_cypherbench(row, schemas[graph]))

    return selected
