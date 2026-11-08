import json
from pathlib import Path
import pandas as pd

from agent import OACypherAgent, base_prompt1
from data import get_dataset

import argparse

#create cli parser
def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_url", default = "endeavorXx/Mind-the-Query", type = str)
    parser.add_argument("--validation_type", default = None, type = str)
    return parser.parse_args()    
    

def main():
    
    args = cli()
    dataset_url = args.dataset_url
    validation_type = args.validation_type
    
    train_dataset, test_dataset = get_dataset(url = dataset_url, validation_type = validation_type)
    
    datapoint = train_dataset[0]
    print("\nQUESTION: \n", datapoint["question"])
    OA_agent = OACypherAgent()
    responses = OA_agent.generate(base_prompt = base_prompt1, batch = [datapoint])
    print("\nAGENT REPONSE: \n", responses)
    print("\n ANSWER:\n ", datapoint["cypher"])
    # print(train_dataset)
if __name__ == "__main__":
    main()
        