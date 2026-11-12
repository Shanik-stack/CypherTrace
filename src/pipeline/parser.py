"""Cypher text to the existing JSON request format, with no file side effects."""
import json
import re
from enum import Enum

from cypherast.ast import AstNode
from cypherast.errors import ParseError
from cypherast.lexer import Lexer, TokenKind
from cypherast.parser import Parser


# Match fences outside quoted Cypher text, identifiers, and comments.
FENCE_OR_QUOTED_TEXT = re.compile(
    r'(?P<fence>^[ \t]*`{3,}(?:[ \t]*cypher\b)?[ \t]*|(?<=[ \t])`{3,}[ \t]*$)'
    r"|`(?:``|[^`])*`|'(?:\\[\s\S]|[^'\\])*'|\"(?:\\[\s\S]|[^\"\\])*\""
    r'|//[^\n]*|/\*[\s\S]*?\*/',
    flags=re.IGNORECASE | re.MULTILINE,
)


class Neo4jParser(Parser):
    """Catch syntax that CypherAST silently changes into a different query."""

    def parse_primary(self):
        token = self._peek()
        if token.kind in (TokenKind.COUNT, TokenKind.IDENT) and self._peek(1).kind is TokenKind.LPAREN:
            if self._peek(2).kind is TokenKind.DISTINCT:
                # The base parser drops DISTINCT in both cases.
                if token.text.lower() == 'count' and self._peek(3).kind is TokenKind.STAR:
                    raise self._err('COUNT(DISTINCT *) is not valid Cypher', code='CG1101')
                if token.text.lower() == 'coalesce':
                    raise self._err('DISTINCT is not allowed in coalesce()', code='CG1101')
        return super().parse_primary()

    def parse_relationship_pattern(self):
        left_arrow = self._check(TokenKind.ARROW_LEFT)
        relationship = super().parse_relationship_pattern()
        token = self.tokens[self._i - 1]
        if left_arrow and token.kind is TokenKind.ARROW_RIGHT:
            raise ParseError('A relationship pattern cannot have two arrowheads',
                             code='CG1101', position=token.position, source=self.source)
        return relationship


def strip_outer_fence(query):
    text = query.strip()
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().lower() in ('```cypher', '```') and lines[-1].strip() == '```':
        text = '\n'.join(lines[1:-1]).strip()
    return text


def normalize_query(query, reject_multiple=False):
    text = strip_outer_fence(query)
    if ';' not in text:
        return text

    first_query = ''
    first_tokens = []
    first_separator = None
    start = 0
    statement_tokens = []
    for token in Lexer(text).tokenize():
        if token.kind in (TokenKind.SEMICOLON, TokenKind.EOF):
            end = token.position.offset
            if statement_tokens:
                if not first_tokens:
                    first_query = text[start:end].strip()
                    first_tokens = statement_tokens
                    first_separator = token.position
                elif statement_tokens != first_tokens:
                    if reject_multiple:
                        raise ParseError('Multiple different statements in one reply. Return one Cypher query.',
                                         code='CG1101', position=first_separator, source=text)
                    return text
            statement_tokens = []
            start = end + 1
        else:
            statement_tokens.append((token.kind, token.text))
    return first_query


def normalize_generated_query(query):
    """Strip Markdown fences from model output and collapse exact fenced repeats."""
    text = strip_outer_fence(query)
    parts = []
    start = 0
    for match in FENCE_OR_QUOTED_TEXT.finditer(text):
        if match['fence'] is not None:
            parts.append(text[start:match.start()].strip())
            start = match.end()
    if not parts:
        return normalize_query(text, reject_multiple=True)
    parts.append(text[start:].strip())
    parts = [part for part in parts if part]
    if parts:
        first_query = normalize_query(parts[0], reject_multiple=True)
        if all(normalize_query(part, reject_multiple=True) == first_query for part in parts[1:]):
            return first_query
    return normalize_query('\n'.join(parts), reject_multiple=True)


def ast_to_json_value(value):
    """Convert CypherAST nodes and enums for JSON's default serializer."""
    if isinstance(value, AstNode):
        return {'type': type(value).__name__, 'args': value.args}
    if isinstance(value, Enum):
        return value.name
    raise TypeError(f'Cannot serialize {type(value).__name__}')


def build_request(query, schema, parameters=None, request_id='query_0'):
    query = normalize_query(query, reject_multiple=True)
    schema = json.loads(schema) if isinstance(schema, str) else schema
    tree = Neo4jParser(query, dialect='neo4j25').parse()
    return {
        'protocol_version': 1, 'request_id': request_id, 'dialect': 'neo4j25',
        'query_text': query, 'query_ast': [json.loads(json.dumps(tree, default=ast_to_json_value))],
        'schema': schema, 'parameters': {} if parameters is None else parameters,
    }
