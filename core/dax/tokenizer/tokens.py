"""DAX token types and Token dataclass."""

from dataclasses import dataclass
from enum import Enum, auto


class TokenType(Enum):
    """DAX token classification."""

    KEYWORD = auto()  # VAR, RETURN, IF, ELSE, SWITCH, TRUE, FALSE, DEFINE, FUNCTION, etc.
    FUNCTION = auto()  # Known DAX function name followed by ( (dotted names: STDEVX.S)
    IDENTIFIER = auto()  # Unquoted name not classified as KEYWORD/FUNCTION
    TABLE_REF = auto()  # 'Quoted Table Name' ('' escapes a literal ')
    COLUMN_REF = auto()  # [Column or Measure] (brackets included, ]] escapes a literal ])
    QUALIFIED_REF = auto()  # 'Table'[Column] (composite; spaces/tabs allowed between)
    STRING = auto()  # "double quoted" with "" escape
    NUMBER = auto()  # Integer, decimal and scientific literals (1, .5, 2., 1e-3)
    OPERATOR = auto()  # + - * / ^ = == <> < > <= >= && || & =>
    PAREN_OPEN = auto()  # (
    PAREN_CLOSE = auto()  # )
    COMMA = auto()  # ,
    COMMENT_LINE = auto()  # // or -- to end of line
    COMMENT_BLOCK = auto()  # /* ... */
    WHITESPACE = auto()  # Spaces, tabs
    NEWLINE = auto()  # \n, \r\n
    DOT = auto()  # . (outside numbers / dotted function names)
    UNKNOWN = auto()  # Unrecognized
    BRACE_OPEN = auto()  # { (table constructor)
    BRACE_CLOSE = auto()  # }
    SEMICOLON = auto()  # ; (locale list separator)
    COLON = auto()  # : (UDF parameter type annotation)
    DATETIME = auto()  # dt"2024-01-01" literal


@dataclass(frozen=True, slots=True)
class Token:
    """A single DAX token with position information.

    ``terminated`` is False only for a delimited token (string, quoted table,
    bracket name, block comment) whose closing delimiter is missing — the only
    lexical error the lexer reports. ``line``/``col`` are where the token STARTS.
    """

    type: TokenType
    value: str
    start: int  # Char offset in source (0-based)
    end: int  # Char offset exclusive
    line: int  # 1-based line number (start of token)
    col: int  # 1-based column number (start of token)
    terminated: bool = True
