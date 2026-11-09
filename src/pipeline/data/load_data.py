from datasets import load_dataset
import json

class MTQ_dataset():
    
    def __init__(self, validation_type = 'manual'):
        #get dataset from HF
        dataset = load_dataset(
            path="endeavorXx/Mind-the-Query",
            name=validation_type
        )

        #update the graph schema in the dataset to follow the new neo4j schema
        dataset = dataset.map(self.normalize_schema)
        self.train_dataset, self.test_dataset = dataset["train"], dataset["test"]
        
    # use to update the MTQ graph schema to match neo4j schema.
    def normalize_schema(self, example):
        schema = json.loads(example["schema"])

        # rename "datatype" to "type" for Neo4j GraphRAG compatibility
        for section in ("node_props", "rel_props"):
            for properties in schema[section].values():
                for prop in properties:
                    if "datatype" in prop:
                        prop["type"] = prop.pop("datatype")

        # Mind-the-Query does not provide constraint/index metadata
        metadata = schema.setdefault("metadata", {})
        metadata.setdefault("constraint", None)
        metadata.setdefault("index", None)

        return {"schema": json.dumps(schema, sort_keys=True)}
    
    def get_schemas(self, split = "test"):
        if split == "test":
            dataset = self.test_dataset
        else: dataset = self.train_dataset
         
        return [datapoint['schema'] for datapoint in dataset]




if __name__ == "__main__":
    MTQ = MTQ_dataset()
    print("get_schema")
    for i in MTQ.get_schemas():
        print("\n----------\n")
        print(i)
        print("\n-------------\n")