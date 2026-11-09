from pathlib import Path
import cypherast
import json
from enum import Enum
from pathlib import Path
from cypherast.ast import AstNode

class cypherast_parser():
    def __init__(self):
        pass
    
    #parse Cypher to AST using Cypherast
    def parse(self, queries: list[str]):
        parser_results = []
        
        for query in queries:
            tree = cypherast.parse_one(query, read="neo4j5")
            parser_results.append(tree)

        return parser_results
    
    #
    def to_json(self, output: list[AstNode], path: Path = Path("src/pipeline/graph/ast_json/cypherast_parsed.json")):
        def encode(obj):
            if isinstance(obj, AstNode):
                return {
                    "type": type(obj).__name__,
                    "args": obj.args
                }

            if isinstance(obj, Enum):
                return obj.name

            raise TypeError(f"Cannot serialize {type(obj).__name__}")

        path.parent.mkdir(parents=True, exist_ok=True)
        
        with path.open("w", encoding="utf-8") as f:
            json.dump(output, f, default=encode, indent=2)



class graphglot_parser():
    def __init__(self):
        from graphglot.dialect import Dialect
        self.neo4j_dialect = Dialect.get_or_raise("neo4j")
        
    #parse Cypher to AST using Graphglot
    def parse(self, queries: list[str]):
        results = []

        for query in queries:
            trees = self.neo4j_dialect.parse(query)

            if len(trees) != 1 or trees[0] is None:
                raise ValueError("Expected exactly one Cypher statement")
            
            #to get just one ast version
            results.append(trees[0])

        return results

    def to_json(self, output: list[str] | None = None, path: Path = Path("src/pipeline/graph/ast_json/graphglot_parsed.json")):
        pass




if __name__ == "__main__":
    queries = ["""
    MATCH (a:Person)-[:KNOWS]->(b:Person)
                    -[:KNOWS]->(c:Person)
    RETURN b
    """]
    CP = cypherast_parser()
    GP = graphglot_parser()
    print("\n\nCYPHER PARSER RESULT: \n ")
    CPr = CP.parse(queries)
    print(CPr)
    print("\n\n GRAPHGLOT PARSER RESULT: \n ")
    GPr = GP.parse(queries)
    # print(GPr)
    print(GPr)
    CP.to_json(CPr)
    GP.to_json(GPr)
    

    