from datasets import load_dataset

# get dataset from hf
def get_dataset(url: str, **kwargs):
    
    dataset = load_dataset(
        f"{url}",
        kwargs["validation_type"]
    ) 
    train_dataset = dataset["train"]
    test_dataset = dataset["test"]
    return train_dataset, test_dataset


if __name__ == "__main__":
    d = get_dataset(url = "endeavorXx/Mind-the-Query", validation_type = "manual")
    print(d)