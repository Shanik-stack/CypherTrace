import json
from enum import Enum
from pathlib import Path
from cypherast.ast import AstNode
from cypherast.errors import ParseError
from cypherast.lexer import TokenKind
from cypherast.parser import Parser


class _neo4j25Parser(Parser):
    def parse_relationship_pattern(self):
        starts_with_left_arrow = self._check(TokenKind.ARROW_LEFT)
        relationship = super().parse_relationship_pattern()
        final_token = self.tokens[self._i - 1]
        if starts_with_left_arrow and final_token.kind is TokenKind.ARROW_RIGHT:
            # Cypherast maps both this form and the valid undirected form to BOTH.
            raise ParseError(
                "Neo4j Cypher 5 does not support relationship patterns with two arrowheads",
                code="CG1101",
                position=final_token.position,
                source=self.source,
            )
        return relationship


class cypherast_parser():
    def __init__(self):
        pass
    
    #parse Cypher to AST using Cypherast
    def parse_to_ast(self, queries: list[str]) -> tuple[list[AstNode], list]:
        parser_results = []
        parsing_errors = []
        for i, query in enumerate(queries):
            try:
                tree = _neo4j25Parser(query, dialect="neo4j25").parse()
            except ParseError as e:
                tree = None
                parsing_errors.append({
                    "index": i,
                    "query": query,
                    "error": str(e)
                })

            parser_results.append(tree)

        return parser_results, parsing_errors
        
    #converts cypherast ast objects to python list of dicts 
    def ast_to_dict(self, ast_list: list[AstNode], path: Path =  Path(__file__).resolve().parent / "ast_json" / "cypherast_parsed.json", write_file = True) -> tuple[list[dict], list]:
        def encode(obj):
            if isinstance(obj, AstNode):
                return {
                    "type": type(obj).__name__,
                    "args": obj.args
                }

            if isinstance(obj, Enum):
                return obj.name

            raise TypeError(f"Cannot serialize {type(obj).__name__}")

        json_text = json.dumps(ast_list, default=encode, indent=2)

        if write_file == True:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json_text, encoding="utf-8")
                
        return json.loads(json_text)
    
    #combines schema and other information to create unified lean request schema
    def build_lean_request(
        self,
        queries: list[str],
        schemas: list[dict | str],
        write_file=True,
        file_name = "cypherast_lean_request"
    ) -> tuple[list[dict], list]:
        path = Path(__file__).resolve().parents[3] / "graph_analysis_requests" / f"{file_name}.json"
        error_path = Path(__file__).resolve().parents[3] / "graph_analysis_requests" / f"{file_name}_errors.json"
        
        if len(queries) != len(schemas): raise ValueError("expected a list with one schema per query")
        
        ast_objects, errors = self.parse_to_ast(queries)
        ast_dicts = self.ast_to_dict(ast_objects, write_file = False)
        lean_requests = []
        error_dict = {"no_errors": len(errors), "errors": errors}
        
        for i, (query, ast, schema) in enumerate(zip(queries, ast_dicts, schemas)):
            if ast is None:
                continue
            if isinstance(schema, str):
                schema = json.loads(schema)
            if not isinstance(schema, dict):
                raise ValueError(f"schema for query_{i} must be a JSON object")
            lean_requests.append({
                "protocol_version": 1,
                "request_id": f"query_{i}",
                "dialect": "neo4j25",
                "query_text": query,
                "query_ast": [ast],
                "schema": schema,
                "parameters": {}
            })
        json_text = json.dumps(lean_requests, indent=2)
        errors_text = json.dumps(error_dict, indent=2)
        

        if write_file == True:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json_text, encoding="utf-8")
            error_path.write_text(errors_text, encoding="utf-8")
            
        return lean_requests, errors



class graphglot_parser():
    def __init__(self):
        from graphglot.dialect import Dialect
        self.neo4j_dialect = Dialect.get_or_raise("neo4j")
        
    #parse Cypher to AST using Graphglot
    def parse_to_ast(self, queries: list[str]):
        results = []

        for query in queries:
            trees = self.neo4j_dialect.parse(query)

            #to get just one ast version
            results.append(trees[0])

        return results

    def to_json(self, output: list[str] | None = None, path: Path = Path("src/pipeline/graph/ast_json/graphglot_parsed.json")):
        pass




if __name__ == "__main__":
    # queries = ["""
    # MATCH (a:Person)-[:KNOWS]->(b:Person)
    #                 -[:KNOWS]->(c:Person)
    # RETURN b
    # """]
    # print("\n\n GRAPHGLOT PARSER RESULT: \n ")
    # GPr = GP.parse(queries)
    # print(GPr)
    # print(GPr)
    
    from pipeline.data.load_data import MTQ_dataset
    
    dataset = MTQ_dataset()
    queries = [dataset.test_dataset[i]['cypher'] for i in range(len(dataset.test_dataset))  ]
    schemas = [dataset.test_dataset[i]['schema'] for i in range(len(dataset.test_dataset))  ]
    
    # CP = cypherast_parser()
    # # GP = graphglot_parser()
    # print("\n\nCYPHER PARSER RESULT: \n ")
    # CPr = CP.build_lean_request(queries, [schema])
    # print(CPr)
    # # GP.to_json(GPr)
    
    
    # parser = graphglot_parser()

    parser = cypherast_parser()

    ast_objects = parser.parse_to_ast(queries)
    ast_dicts = parser.ast_to_dict(ast_objects, write_file=False)
    
    requests, errrors = parser.build_lean_request(queries = queries, schemas = schemas)
    