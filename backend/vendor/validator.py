"""
validator.py — Static pre-flight validation for WorldQuant FastExpr alphas.

Purpose: catch the mistakes that dominate ace.log BEFORE any simulation runs, so
a single malformed expression never cancels its multi-simulation siblings (the
biggest source of wasted quota). Checks, in rough order of how many log failures
they prevent:

  MULTI_FIELD        more or fewer than exactly one raw datafield
  BAD_LOOKBACK       a window/lookback argument that isn't a positive integer
                     (e.g. ts_rank(x, 0.5))
  EVENT_INPUT        a ts_*/group_* operator applied to an event-type field
  UNKNOWN_OPERATOR   operator not in the live operator list — kills hallucinations
                     like `neg`, `vec_median`, `ts_stddev`, `ts_lag`, `ts_return`
  ARITY              wrong number of arguments vs the operator signature
  INVALID_GROUP      a group argument that isn't a valid group identifier
  VECTOR_NO_REDUCE   a VECTOR field used without a vec_* reduction
  MATRIX_VEC         a vec_* operator applied to a MATRIX field
  UNKNOWN_FIELD      an identifier used as a field that isn't in the fetched set
  PARSE_ERROR        the expression could not be parsed at all

Everything degrades gracefully: when operator signatures or field metadata are
missing, the affected check is skipped rather than producing a false positive.

Typical usage in a notebook:

    import validator as V
    val = V.build_validator(s, datafields_df, alpha_type="REGULAR")
    valid_exprs, rejected = val.partition(expression_list)
    print(val.report(expression_list))       # {'valid': .., 'rejected': .., 'by_code': {..}}

SuperAlpha (SELECTION / COMBO) expressions
------------------------------------------
SuperAlphas are built from two non-FastExpr expression kinds, and the grammar
here covers both:

  SELECTION  filters the alpha pool by ALPHA ATTRIBUTES, not datafields, using
             string literals and boolean chains:
                 in(datacategories, "news") && turnover >= 0.5
                 && os_start_date > "2020-01-01"
  COMBO      weights the selected alphas, and may be a SEQUENCE of statements
             with assignment and attribute access:
                 stats = generate_stats(alpha); ts_ir(stats.returns, 500)

Neither references datafields, so `require_single_field`, `known_fields` and the
vec_*/VECTOR rules MUST be off for them (see `build_super_validator`). What is
still checked — and what actually catches LLM mistakes here — is parsing,
operator existence against the live SELECTION/COMBO scope, and arity. Bare
identifiers (turnover, os_start_date, alpha, stats.returns) are accepted rather
than guessed at: BRAIN's attribute vocabulary is not exposed by any API we can
read, so rejecting an unlisted one would throw away valid work.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

DEFAULT_GROUPS = {
    "industry", "subindustry", "market", "exchange", "currency", "sector",
}

# Signature parameter names that must receive a positive integer.
INT_PARAM_HINTS = {
    "d", "days", "window", "n", "periods", "period", "lookback",
    "l", "num", "count", "lag", "length", "std",
}

# Window/lookback parameters that MUST be passed as a positional bare integer,
# never as a keyword (FastExpr convention: ts_rank(x, 20), not ts_rank(x, d=20)).
# Other keyword attributes (std, k, constant, ...) are legitimately name=value.
WINDOW_HINTS = {"d", "days", "window", "lookback", "periods", "period", "length", "lag", "l"}

# Signature parameter names that must receive a valid group identifier.
GROUP_PARAM_HINTS = {"group", "groups", "by"}

_NON_FIELD_LITERALS = {"true", "false", "nan"}


# ─────────────────────────────────────────────────────────────────────────────
# Tokenizer + parser (tolerant of infix/ternary FastExpr, e.g. if_else(a>0,1,-1))
# ─────────────────────────────────────────────────────────────────────────────

# `str` and `;` exist for SuperAlpha expressions: SELECTION uses quoted literals
# (in(datacategories, "news"), os_start_date > "2020-01-01") and COMBO may be a
# statement sequence (stats = generate_stats(alpha); ts_ir(stats.returns, 500)).
# Both are harmless in REGULAR FastExpr, which simply never produces them.
_TOKEN_RE = re.compile(
    r"""
    (?P<num>\d+\.\d+|\d+)                       |
    (?P<str>"[^"]*"|'[^']*')                    |
    (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)          |
    (?P<op>>=|<=|==|!=|&&|\|\||[-+*/%<>!?:=])    |
    (?P<punct>[(),;])                           |
    (?P<ws>\s+)
    """,
    re.VERBOSE,
)


class ParseError(Exception):
    pass


@dataclass
class Num:
    raw: str

    @property
    def value(self) -> float:
        return float(self.raw)

    @property
    def is_int(self) -> bool:
        return float(self.raw).is_integer()


@dataclass
class Str:
    """A quoted literal, e.g. "2020-01-01" in a SELECTION expression. Never a
    datafield, an operator, or a group — it contributes nothing to any check."""
    raw: str

    @property
    def value(self) -> str:
        return self.raw[1:-1]


@dataclass
class Ident:
    name: str


@dataclass
class Call:
    name: str
    args: list


@dataclass
class Seq:
    """A `;`-separated statement sequence (COMBO expressions only)."""
    statements: list


@dataclass
class Unary:
    op: str
    operand: object


@dataclass
class BinOp:
    op: str
    left: object
    right: object


@dataclass
class KwArg:
    name: str
    value: object


def _tokenize(text: str):
    pos, toks = 0, []
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise ParseError(f"unexpected character at {pos}: {text[pos:pos + 8]!r}")
        pos = m.end()
        if m.lastgroup != "ws":
            toks.append((m.lastgroup, m.group()))
    return toks


class _Parser:
    """Precedence-agnostic parser. Grouping is irrelevant here — we only need a
    tree whose Call/Ident/Num leaves are all discoverable, so every binary op is
    treated left-associative with a single precedence."""

    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def _next(self):
        t = self._peek()
        self.i += 1
        return t

    def parse(self):
        # A COMBO expression may be several `;`-separated statements; everything
        # else is a single expression and takes the fast path below.
        stmts = [self._expr()]
        while self._peek() == ("punct", ";"):
            self._next()
            if self.i >= len(self.toks):
                break               # tolerate a trailing ';'
            stmts.append(self._expr())
        if self.i != len(self.toks):
            raise ParseError("trailing tokens")
        return stmts[0] if len(stmts) == 1 else Seq(stmts)

    def _expr(self, min_bp: int = 0):
        left = self._prefix()
        while True:
            kind, val = self._peek()
            if kind != "op":
                break
            bp = 1
            if bp < min_bp:
                break
            self._next()
            right = self._expr(bp + 1)
            left = BinOp(val, left, right)
        return left

    def _prefix(self):
        kind, val = self._peek()
        if kind == "op" and val in ("-", "+", "!"):
            self._next()
            return Unary(val, self._prefix())
        return self._primary()

    def _primary(self):
        kind, val = self._peek()
        if kind == "num":
            self._next()
            return Num(val)
        if kind == "str":
            self._next()
            return Str(val)
        if kind == "ident":
            self._next()
            if self._peek() == ("punct", "("):
                return self._call(val)
            return Ident(val)
        if (kind, val) == ("punct", "("):
            self._next()
            node = self._expr()
            if self._next() != ("punct", ")"):
                raise ParseError("expected ')'")
            return node
        raise ParseError(f"unexpected token {val!r}")

    def _call(self, name):
        self._next()  # consume '('
        args = []
        if self._peek() == ("punct", ")"):
            self._next()
            return Call(name, args)
        while True:
            args.append(self._argument())
            kind, val = self._next()
            if (kind, val) == ("punct", ")"):
                break
            if (kind, val) != ("punct", ","):
                raise ParseError(f"expected ',' or ')', got {val!r}")
        return Call(name, args)

    def _argument(self):
        # Keyword argument: ident '=' expr  (a single '=', not '==').
        kind, val = self._peek()
        if (
            kind == "ident"
            and self.i + 1 < len(self.toks)
            and self.toks[self.i + 1] == ("op", "=")
        ):
            self.i += 2  # consume ident and '='
            return KwArg(val, self._expr())
        return self._expr()


def parse(expr: str):
    return _Parser(_tokenize(expr)).parse()


def _count_calls(node, ignore_prefixes=()) -> int:
    """Number of operator applications (Call nodes) in an expression tree.

    `ignore_prefixes` (e.g. ('vec_',)) skips operators whose name starts with one of
    those prefixes when tallying — used so mechanical VECTOR reductions the template
    engine auto-inserts don't eat into the user's operator budget."""
    if isinstance(node, Call):
        self_count = 0 if (ignore_prefixes and node.name.lower().startswith(ignore_prefixes)) else 1
        return self_count + sum(_count_calls(a, ignore_prefixes) for a in node.args)
    if isinstance(node, KwArg):
        return _count_calls(node.value, ignore_prefixes)
    if isinstance(node, Unary):
        return _count_calls(node.operand, ignore_prefixes)
    if isinstance(node, BinOp):
        return _count_calls(node.left, ignore_prefixes) + _count_calls(node.right, ignore_prefixes)
    if isinstance(node, Seq):
        return sum(_count_calls(st, ignore_prefixes) for st in node.statements)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Operator registry (signatures parsed from the live operator list)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OpSig:
    name: str
    min_args: int
    max_args: int      # -1 == unbounded (varargs / '...')
    param_names: list  # best-effort; may be shorter than max_args


def _split_top_level(s: str):
    out, depth, buf = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _parse_signature(name: str, definition: str) -> OpSig:
    """Parse an operator's arg list from its definition string, e.g. 'ts_rank(x, d)'.
    When no parenthesised signature is found, arity is left unconstrained."""
    src = definition or ""
    idx = src.find(name + "(")
    start = idx + len(name) if idx != -1 else src.find("(")
    if start == -1 or start >= len(src) or src[start] != "(":
        return OpSig(name, 0, -1, [])

    depth, end = 0, -1
    for j in range(start, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                end = j
                break
    if end == -1:
        return OpSig(name, 0, -1, [])

    inner = src[start + 1:end].strip()
    if inner == "":
        return OpSig(name, 0, 0, [])

    param_names, required, optional, varargs = [], 0, 0, False
    for raw in _split_top_level(inner):
        p = raw.strip()
        if not p:
            continue
        if "..." in p or p.endswith("*"):
            varargs = True
            continue
        head = re.split(r"[=:]", p, 1)[0]
        head = re.sub(r"[\[\]<>]", " ", head).strip()
        pname = head.split()[-1].lower() if head.split() else ""
        param_names.append(pname)
        if "=" in p:
            optional += 1
        else:
            required += 1

    max_args = -1 if varargs else required + optional
    return OpSig(name, required, max_args, param_names)


class OperatorRegistry:
    def __init__(self, sigs: dict):
        self._sigs = sigs
        # case-insensitive lookup: operators typed as TS_RANK / Ts_Rank still resolve
        self._lower = {k.lower(): v for k, v in sigs.items()}

    def __contains__(self, name):
        return name in self._sigs or str(name).lower() in self._lower

    def get(self, name):
        return self._sigs.get(name) or self._lower.get(str(name).lower())

    def __len__(self):
        return len(self._sigs)

    @classmethod
    def from_dataframe(cls, ops_df, name_col=None, def_col=None):
        cols = list(ops_df.columns)
        name_col = name_col or next((c for c in ("name", "id") if c in cols), None)
        def_col = def_col or next(
            (c for c in ("definition", "signature", "description", "desc") if c in cols), None
        )
        if name_col is None:
            raise ValueError("operator DataFrame has no 'name'/'id' column")

        sigs: dict = {}
        for _, row in ops_df.iterrows():
            nm = str(row[name_col]).strip()
            if not nm or nm.lower() == "nan":
                continue
            definition = ""
            if def_col is not None:
                v = row[def_col]
                definition = "" if v is None else str(v)
            # scope-exploded rows duplicate operators; keep the first signature.
            sigs.setdefault(nm, _parse_signature(nm, definition))
        return cls(sigs)


# ─────────────────────────────────────────────────────────────────────────────
# Validator
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Issue:
    code: str
    message: str

    def __str__(self):
        return f"{self.code}: {self.message}"


@dataclass
class Result:
    expr: str
    ok: bool
    issues: list

    def __bool__(self):
        return self.ok


class Validator:
    def __init__(
        self,
        registry: OperatorRegistry,
        known_fields=None,
        field_types=None,
        event_fields=None,
        valid_groups=None,
        require_single_field: bool = True,
        check_unknown_kwargs: bool = False,
        max_operators=None,
        op_count_ignore_prefixes=None,
        treat_identifiers_as_fields: bool = True,
        invalid_identifiers=None,
    ):
        self.reg = registry
        # Identifiers KNOWN to be rejected by the platform (see
        # SELECTION_NON_VARIABLES). Flagged locally so the mistake surfaces
        # instantly instead of after a wasted simulation that fails with
        # 'Attempted to use unknown variable'.
        self.invalid_identifiers = {str(x).lower() for x in (invalid_identifiers or ())}
        # SELECTION/COMBO expressions name alpha ATTRIBUTES (turnover, os_start_date,
        # alpha, stats.returns), never datafields — so every datafield-shaped check
        # (VECTOR reduction, MATRIX/vec_, unknown-field, single-field) is switched off
        # wholesale rather than being coincidentally inert.
        self.treat_identifiers_as_fields = treat_identifiers_as_fields
        self.known_fields = set(known_fields) if known_fields else None
        self.field_types = field_types or {}          # id -> MATRIX/VECTOR/...
        self.event_fields = set(event_fields) if event_fields else set()
        self.valid_groups = set(valid_groups) if valid_groups else set(DEFAULT_GROUPS)
        self.require_single_field = require_single_field
        self.max_operators = max_operators            # cap on operators per expression
        # operator-name prefixes NOT counted toward max_operators (e.g. ('vec_',))
        self.op_count_ignore_prefixes = tuple(op_count_ignore_prefixes or ())
        # Off by default: operator definitions don't always enumerate optional
        # keyword attributes, so an unknown-kwarg check risks false positives.
        self.check_unknown_kwargs = check_unknown_kwargs

    # -- public -------------------------------------------------------------
    def validate(self, expr: str) -> Result:
        try:
            ast = parse(expr)
        except ParseError as e:
            return Result(expr, False, [Issue("PARSE_ERROR", str(e))])

        issues: list = []
        fields: list = []
        self._walk(ast, issues, fields, parent_call=None)

        if self.max_operators is not None:
            nops = _count_calls(ast, self.op_count_ignore_prefixes)
            if nops > self.max_operators:
                issues.append(Issue(
                    "TOO_MANY_OPERATORS",
                    f"uses {nops} operators; keep it to at most {self.max_operators} "
                    f"(simpler, more diverse alphas)",
                ))

        if self.require_single_field:
            distinct = set(fields)
            if len(distinct) == 0:
                issues.append(Issue("MULTI_FIELD", "no raw datafield found (expected exactly 1)"))
            elif len(distinct) > 1:
                issues.append(Issue(
                    "MULTI_FIELD",
                    f"expected exactly 1 datafield, found {len(distinct)}: {sorted(distinct)}",
                ))

        return Result(expr, len(issues) == 0, issues)

    def partition(self, exprs):
        valid, rejected = [], []
        for e in exprs:
            r = self.validate(e)
            (valid.append(e) if r.ok else rejected.append((e, r.issues)))
        return valid, rejected

    def report(self, exprs) -> dict:
        valid, rejected = self.partition(exprs)
        codes = Counter(i.code for _, issues in rejected for i in issues)
        return {
            "total": len(exprs),
            "valid": len(valid),
            "rejected": len(rejected),
            "by_code": dict(codes.most_common()),
        }

    # -- internals ----------------------------------------------------------
    def _is_field(self, name: str) -> bool:
        if name in self.valid_groups or name in _NON_FIELD_LITERALS:
            return False
        if self.known_fields is not None:
            return name in self.known_fields
        return name not in self.reg  # heuristic when no field set is supplied

    def _walk(self, node, issues, fields, parent_call):
        if isinstance(node, (Num, Str)):
            return
        if isinstance(node, Seq):
            for st in node.statements:
                self._walk(st, issues, fields, parent_call=None)
            return
        if isinstance(node, Ident):
            self._check_ident(node, issues, fields, parent_call)
            return
        if isinstance(node, Unary):
            self._walk(node.operand, issues, fields, parent_call=None)
            return
        if isinstance(node, BinOp):
            self._walk(node.left, issues, fields, parent_call=None)
            self._walk(node.right, issues, fields, parent_call=None)
            return
        if isinstance(node, Call):
            positional = [a for a in node.args if not isinstance(a, KwArg)]
            kwargs = [a for a in node.args if isinstance(a, KwArg)]
            self._check_call(node, positional, kwargs, issues)
            sig = self.reg.get(node.name)
            for k, a in enumerate(positional):
                pname = sig.param_names[k] if (sig and k < len(sig.param_names)) else None
                self._check_arg(node, k, a, pname, issues)
                self._walk(a, issues, fields, parent_call=node)
            for kw in kwargs:
                self._check_kwarg(node, kw, sig, issues)
                self._walk(kw.value, issues, fields, parent_call=None)
            return

    def _check_ident(self, node, issues, fields, parent_call):
        nm = node.name
        if self.invalid_identifiers and nm.lower() in self.invalid_identifiers:
            issues.append(Issue(
                "INVALID_SELECTION_VAR",
                f"{nm!r} is not a selection attribute — the platform rejects it. "
                f"An alpha's performance (sharpe, fitness, returns) cannot be filtered on.",
            ))
            return
        if not self.treat_identifiers_as_fields:
            return
        if nm in self.valid_groups or nm in _NON_FIELD_LITERALS:
            return
        if self.known_fields is not None and nm not in self.known_fields:
            # Not a known field and not a group/literal -> flag, but still count
            # it so the single-field check reflects reality.
            issues.append(Issue("UNKNOWN_FIELD", f"unknown datafield {nm!r}"))
            fields.append(nm)
            return
        if not self._is_field(nm):
            return

        fields.append(nm)
        ftype = str(self.field_types.get(nm, "")).upper()
        # lowercase the parent operator name so prefix rules (ts_/group_/vec_) apply
        # regardless of how the operator was capitalised.
        pname = parent_call.name.lower() if parent_call is not None else None

        if nm in self.event_fields and pname and pname.startswith(("ts_", "group_")):
            issues.append(Issue(
                "EVENT_INPUT",
                f"operator {pname!r} does not support event input {nm!r}",
            ))
        if ftype == "VECTOR" and pname and not pname.startswith("vec_"):
            issues.append(Issue(
                "VECTOR_NO_REDUCE",
                f"VECTOR field {nm!r} must be reduced by a vec_* operator before {pname!r}",
            ))
        if ftype == "MATRIX" and pname and pname.startswith("vec_"):
            issues.append(Issue(
                "MATRIX_VEC",
                f"vec_* operator {pname!r} cannot be applied to MATRIX field {nm!r}",
            ))

    def _check_call(self, node, positional, kwargs, issues):
        if node.name not in self.reg:
            issues.append(Issue("UNKNOWN_OPERATOR", f"unknown operator {node.name!r}"))
            return
        sig = self.reg.get(node.name)
        if sig is None:
            return
        n_pos = len(positional)
        # A supplied keyword counts toward the total, so a missing required
        # attribute (e.g. kth_element(x, d) without k) surfaces as too-few args.
        provided = n_pos + len({kw.name for kw in kwargs})
        rng = str(sig.min_args) if sig.min_args == sig.max_args else f"{sig.min_args}-{sig.max_args}"
        if provided < sig.min_args:
            rng = f"at least {sig.min_args}" if sig.max_args == -1 else rng
            issues.append(Issue("ARITY", f"{node.name} expects {rng} args, got {provided}"))
        elif sig.max_args != -1 and n_pos > sig.max_args:
            issues.append(Issue(
                "ARITY", f"{node.name} expects {rng} args, got {n_pos} positional"))

        # HARD RULE (templates + LLM): a vec_* operator reduces a VECTOR datafield to
        # a scalar, so its data input must be a RAW VECTOR field — never another
        # operator/expression, a number, a group, or a non-VECTOR field.
        if node.name.lower().startswith("vec_") and positional:
            arg0 = positional[0]
            if isinstance(arg0, Ident):
                nm = arg0.name
                ftype = str(self.field_types.get(nm, "")).upper()
                if nm in self.valid_groups or nm in _NON_FIELD_LITERALS:
                    issues.append(Issue(
                        "VEC_BAD_INPUT",
                        f"vec_* operator {node.name!r} must wrap a VECTOR datafield, not {nm!r}"))
                elif ftype and ftype != "VECTOR":
                    issues.append(Issue(
                        "VEC_BAD_INPUT",
                        f"vec_* operator {node.name!r} can only wrap a VECTOR field; {nm!r} is {ftype}"))
            else:
                issues.append(Issue(
                    "VEC_BAD_INPUT",
                    f"vec_* operator {node.name!r} must wrap a raw VECTOR datafield directly, "
                    f"not an expression/number"))

    def _check_arg(self, call, k, arg, pname, issues):
        if pname and pname in INT_PARAM_HINTS and isinstance(arg, Num):
            if (not arg.is_int) or arg.value < 1:
                issues.append(Issue(
                    "BAD_LOOKBACK",
                    f"{call.name} arg {k + 1} ({pname}) must be a positive integer, got {arg.raw}",
                ))
        if pname and pname in GROUP_PARAM_HINTS:
            # The group argument must be a group identifier (industry, sector, …),
            # optionally a classification datafield or a bucket()/densify() call —
            # NEVER a number. group_zscore(x, 0) is a real bug the LLM produces.
            if isinstance(arg, Num):
                issues.append(Issue(
                    "INVALID_GROUP",
                    f"{call.name} arg {k + 1} ({pname}) must be a group identifier "
                    f"(one of {sorted(self.valid_groups)}), not the number {arg.raw}",
                ))
            elif isinstance(arg, Ident):
                known = self.known_fields is not None and arg.name in self.known_fields
                if arg.name not in self.valid_groups and not known:
                    issues.append(Issue(
                        "INVALID_GROUP",
                        f"{call.name} arg {k + 1} ({pname}) must be one of "
                        f"{sorted(self.valid_groups)}, got {arg.name!r}",
                    ))
            elif not isinstance(arg, Call):
                # a bare literal or arithmetic expression can never be a group
                issues.append(Issue(
                    "INVALID_GROUP",
                    f"{call.name} arg {k + 1} ({pname}) must be a group identifier "
                    f"(one of {sorted(self.valid_groups)})",
                ))

    def _check_kwarg(self, call, kw, sig, issues):
        name = kw.name.lower()
        # FastExpr rule: window/lookback must be positional, never a keyword.
        if name in WINDOW_HINTS:
            issues.append(Issue(
                "LOOKBACK_AS_KEYWORD",
                f"{call.name}: window/lookback {kw.name!r} must be a positional "
                f"integer (e.g. {call.name}(x, 20)), not a keyword",
            ))
        # FastExpr rule: groups are passed DIRECTLY (positionally), like lookback —
        # never as a keyword (e.g. group_zscore(x, industry), not group=industry).
        if name in GROUP_PARAM_HINTS:
            issues.append(Issue(
                "GROUP_AS_KEYWORD",
                f"{call.name}: group {kw.name!r} must be a positional argument "
                f"(e.g. {call.name}(x, industry)), not a keyword",
            ))
        # Only enforce for BOUNDED signatures (max_args != -1) — there we know the
        # full parameter list, so an unknown keyword is a genuine error (e.g. hump=
        # on ts_rank). Varargs operators have partial signatures, so we skip them.
        if self.check_unknown_kwargs and sig and sig.param_names and sig.max_args != -1:
            valid = {p.lower() for p in sig.param_names if p}
            if name not in valid and name not in WINDOW_HINTS and name not in GROUP_PARAM_HINTS:
                issues.append(Issue(
                    "UNKNOWN_KWARG",
                    f"{call.name} has no attribute {kw.name!r}; valid attributes: {sorted(valid)}",
                ))


# ─────────────────────────────────────────────────────────────────────────────
# Convenience builder
# ─────────────────────────────────────────────────────────────────────────────

def build_validator(
    session=None,
    datafields_df=None,
    ops_df=None,
    alpha_type: str = "REGULAR",
    valid_groups=None,
    event_fields=None,
    require_single_field: bool = True,
    max_operators=None,
) -> Validator:
    """
    Build a Validator from a live session (fetches operators) and/or a datafields
    DataFrame. Either `session` or `ops_df` must be provided.
    """
    if ops_df is None:
        if session is None:
            raise ValueError("provide either ops_df or a session")
        import ace_lib as ace
        ops_df = ace.get_operators(session)
        if "scope" in ops_df.columns and (ops_df["scope"] == alpha_type).any():
            ops_df = ops_df[ops_df["scope"] == alpha_type]

    registry = OperatorRegistry.from_dataframe(ops_df)

    known_fields = None
    field_types = None
    if datafields_df is not None and "id" in getattr(datafields_df, "columns", []):
        ids = datafields_df["id"].dropna().astype(str)
        known_fields = set(ids)
        if "type" in datafields_df.columns:
            field_types = dict(zip(ids, datafields_df["type"].astype(str)))

    return Validator(
        registry,
        known_fields=known_fields,
        field_types=field_types,
        event_fields=event_fields,
        valid_groups=valid_groups,
        require_single_field=require_single_field,
        max_operators=max_operators,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SuperAlpha: SELECTION + COMBO
# ─────────────────────────────────────────────────────────────────────────────

# Alpha attributes usable in a SELECTION expression, mapped to the syntax each
# one takes. This is DOCUMENTATION fed to the LLM — it is NOT used to reject
# expressions, since BRAIN exposes no endpoint listing its vocabulary and an
# unlisted-but-valid attribute must never cost the user a discarded selection.
#
# VERIFIED, not guessed: every entry below was probed against BRAIN's
# /simulations/super-selection endpoint and accepted. Plausible-sounding
# attributes that BRAIN actually REJECTS with 'Attempted to use unknown
# variable' are listed in SELECTION_NON_VARIABLES so the prompt can warn the
# model off them — sharpe/fitness/returns in particular read as obvious and are
# not real, so an LLM left to its own devices reaches for them constantly.
SELECTION_VARIABLES = {
    # `own` restricts the pool to the user's OWN alphas. It is a bare condition
    # (no operator, no value) and in practice starts almost every selection.
    "own": "bare condition, e.g. own",
    "turnover": "number, e.g. turnover < 0.2",
    "decay": "number, e.g. decay >= 4",
    "truncation": "number, e.g. truncation > 0",
    "datafield_count": "number, e.g. datafield_count <= 3",
    "operator_count": "number, e.g. operator_count < 8",
    "self_correlation": "number, e.g. self_correlation < 0.7",
    "prod_correlation": "number, e.g. prod_correlation < 0.5",
    "universe": 'string, e.g. universe == "TOP3000"',
    "neutralization": 'string, e.g. neutralization == "INDUSTRY"',
    "color": 'string, e.g. color == "GREEN"',
    "os_start_date": 'date string, e.g. os_start_date > "2020-01-01"',
    "datacategories": 'membership, e.g. in(datacategories, "news")',
    "datafields": 'membership, e.g. in(datafields, "close")',
    "tags": 'membership, e.g. in(tags, "ace_winner")',
}

# Attributes BRAIN rejects outright. Kept explicitly so the prompt can forbid
# them by name rather than hoping the model infers their absence.
SELECTION_NON_VARIABLES = [
    "sharpe", "fitness", "returns", "margin", "drawdown", "pnl", "longCount",
    "shortCount", "delay", "region", "operators", "dataset", "is_start_date",
    "date_created", "instrument_type", "type", "language", "theme",
]

# Identifiers a COMBO expression can reference. `alpha` is the per-component
# alpha handle; `generate_stats(alpha)` yields a stats object whose members
# (.returns, .pnl, .sharpe …) are read with attribute access.
COMBO_VARIABLES = ["alpha", "stats", "generate_stats"]


def build_super_validator(ops_df, kind: str = "SELECTION", max_operators=None) -> Validator:
    """Validator for a SuperAlpha SELECTION or COMBO expression.

    `ops_df` should already be narrowed to that scope (app.py's `_registry` does
    this from the live operator list). Datafield semantics are off entirely —
    what remains is parsing, operator existence and arity, which is exactly what
    catches an LLM inventing `ts_information_ratio` or mis-arg-ing `ts_ir`.
    """
    kind = (kind or "SELECTION").upper()
    if kind not in ("SELECTION", "COMBO"):
        raise ValueError("kind must be 'SELECTION' or 'COMBO'")
    return Validator(
        OperatorRegistry.from_dataframe(ops_df) if not isinstance(ops_df, OperatorRegistry) else ops_df,
        known_fields=None,
        field_types=None,
        require_single_field=False,
        check_unknown_kwargs=False,   # selection/combo signatures are sparsely documented
        max_operators=max_operators,
        treat_identifiers_as_fields=False,
        # Only SELECTION has a verified reject-list; COMBO's vocabulary
        # (alpha, stats.*) is open, so nothing is pre-rejected there.
        invalid_identifiers=(SELECTION_NON_VARIABLES if kind == "SELECTION" else None),
    )
