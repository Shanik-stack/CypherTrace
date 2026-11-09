from datasets import load_dataset
import json

# use to update the MTQ graph schema to match neo4j schema.
def normalize_schema(example):
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


# get dataset from hf
def get_MTQ_dataset(url: str = "endeavorXx/Mind-the-Query", **kwargs):
    validation_type = kwargs.get("validation_type")
    
    

    dataset = load_dataset(
        path=url,
        name=validation_type
    )

    #update the graph schema in the dataset to follow the new neo4j schema
    dataset = dataset.map(normalize_schema)

    return dataset["train"], dataset["test"]




if __name__ == "__main__":
    d = get_MTQ_dataset(url = "endeavorXx/Mind-the-Query", validation_type = "manual")
    print(d)
    print(d[0][0])