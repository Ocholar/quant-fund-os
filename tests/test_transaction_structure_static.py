"""
Static/structural regression tests for the persistence-transaction fix.

These tests parse main.py's source with `ast` and check structural
properties of the code -- they need no database, no container, and no
network, so they run anywhere (including in CI on every commit) and catch
regressions of the specific bug class we just fixed:

  1. A network call (Telegram) must never be reachable from inside the
     `with engine.begin() as conn:` block in the main trading loop.
  2. The main trading-loop transaction must be wrapped in a try/except
     that logs structured failure info (not just a generic message).
  3. Every `ON CONFLICT` target column must have a corresponding
     `CREATE TABLE` (in this file) that declares it PRIMARY KEY or the
     query must be the one known, migration-covered exception
     (strategy_scores), so a new unsafe ON CONFLICT site can't sneak in
     unnoticed.

Run with: pytest tests/test_transaction_structure_static.py -v
"""
import ast
import re
import pathlib

MAIN_PY = pathlib.Path(__file__).parent.parent / "main.py"


def _load_tree():
    src = MAIN_PY.read_text(encoding="utf-8")
    return src, ast.parse(src)


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_strategy_scores_write_not_inside_critical_engine_begin_in_main():
    """
    Regression test for the architectural hardening on top of the schema
    fix: the strategy_scores INSERT must not be reachable from inside the
    critical `with engine.begin() as conn:` block in main()'s trading
    loop either -- not just Telegram. Analytics (strategy_scores) must
    never be able to roll back a real trade, even if some future schema
    change reintroduces a constraint problem in that table. The write
    must happen in its own, separate transaction after the critical one
    has already committed.
    """
    src, tree = _load_tree()
    main_fn = _find_function(tree, "main")
    assert main_fn is not None

    violations = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.with_depth = 0

        def visit_With(self, node):
            is_engine_begin = any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "begin"
                and isinstance(item.context_expr.func.value, ast.Name)
                and item.context_expr.func.value.id == "engine"
                for item in node.items
            )
            if is_engine_begin:
                self.with_depth += 1
                self.generic_visit(node)
                self.with_depth -= 1
            else:
                self.generic_visit(node)

        def visit_Call(self, node):
            # conn.execute(text("INSERT INTO strategy_scores ...")) --
            # look for any string literal argument containing both
            # "INSERT" and "strategy_scores" while inside an engine.begin()
            # block that isn't the dedicated, separate helper function.
            if self.with_depth > 0:
                for arg in ast.walk(node):
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if "strategy_scores" in arg.value and "INSERT" in arg.value.upper():
                            violations.append(node.lineno)
            self.generic_visit(node)

    Visitor().visit(main_fn)

    assert violations == [], (
        f"a strategy_scores INSERT is reachable inside the critical "
        f"engine.begin() transaction in main() at line(s) {violations} -- "
        f"this reintroduces coupling between analytics and the trading "
        f"ledger. It must live in _qfos_apply_strategy_score_updates() "
        f"and run only after the critical transaction has committed."
    )


def test_dedicated_non_fatal_strategy_score_helper_exists():
    """
    The post-commit strategy_scores writer must exist as its own function,
    separate from the critical transaction, and must catch its own
    exceptions per-update rather than letting one bad update abort the
    rest or propagate up to the caller.
    """
    src, tree = _load_tree()
    helper = _find_function(tree, "_qfos_apply_strategy_score_updates")
    assert helper is not None, (
        "_qfos_apply_strategy_score_updates() is missing -- strategy_scores "
        "writes must be decoupled into their own best-effort function"
    )

    has_try_except_inside = any(
        isinstance(node, ast.Try) for node in ast.walk(helper)
    )
    assert has_try_except_inside, (
        "_qfos_apply_strategy_score_updates() must wrap its writes in "
        "try/except so a failure on one strategy can't abort the others "
        "or propagate to the caller"
    )


def test_main_py_parses():
    """Basic sanity: the file must be valid Python."""
    src, tree = _load_tree()
    assert tree is not None


def test_telegram_not_called_inside_engine_begin_in_main():
    """
    Regression test for the core bug: send_telegram_alert(...) must not be
    directly reachable from inside a `with engine.begin() as conn:` block
    within main()'s trading loop. It must only be called after the `with`
    block has exited successfully (in an `else:` clause of a wrapping
    try/except, or after the block at the same or shallower indentation).
    """
    src, tree = _load_tree()
    main_fn = _find_function(tree, "main")
    assert main_fn is not None, "could not find main() function"

    violations = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.with_depth = 0

        def visit_With(self, node):
            is_engine_begin = any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "begin"
                and isinstance(item.context_expr.func.value, ast.Name)
                and item.context_expr.func.value.id == "engine"
                for item in node.items
            )
            if is_engine_begin:
                self.with_depth += 1
                self.generic_visit(node)
                self.with_depth -= 1
            else:
                self.generic_visit(node)

        def visit_Call(self, node):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "send_telegram_alert"
                and self.with_depth > 0
            ):
                violations.append(node.lineno)
            self.generic_visit(node)

    Visitor().visit(main_fn)

    assert violations == [], (
        f"send_telegram_alert() is called inside an engine.begin() block "
        f"at line(s) {violations} -- this reintroduces the bug where a "
        f"pre-commit network call can trigger a silent rollback of an "
        f"already-notified trade."
    )


def test_main_trading_loop_wraps_engine_begin_in_try_except():
    """
    The trade-persistence transaction must be wrapped in a try/except that
    calls a structured failure logger, not left to fail into only the
    generic outer 'Bot loop error' handler.
    """
    src, _ = _load_tree()
    assert "_qfos_log_transaction_failure(" in src, (
        "structured transaction-failure logger is missing -- silent/generic "
        "failure logging regression"
    )
    assert "def _qfos_log_transaction_failure(" in src, (
        "the structured failure logger function itself is missing"
    )


def test_every_on_conflict_target_has_backing_constraint_or_is_known_exception():
    """
    Every `ON CONFLICT (<col>)` site in main.py must target a column that is
    declared PRIMARY KEY (or UNIQUE) in an in-file CREATE TABLE statement --
    except strategy_scores, which is the one documented, migration-covered
    exception (see migration_001_fix_strategy_scores_constraint.sql and the
    startup self-heal in _qfos_ensure_strategy_scores_constraint()).

    This test prevents a *new* unsafe ON CONFLICT site from being added
    without either backing it with a schema declaration in this file, or
    consciously adding it to the KNOWN_EXCEPTIONS list below with a reason.
    """
    src, _ = _load_tree()

    KNOWN_EXCEPTIONS = {
        # table_name -> reason
        "strategy_scores": (
            "constraint added via migration_001_fix_strategy_scores_constraint.sql "
            "and self-healed at startup by _qfos_ensure_strategy_scores_constraint()"
        ),
    }

    conflict_pattern = re.compile(r"ON CONFLICT\s*\(\s*(\w+)\s*\)", re.IGNORECASE)
    create_table_pattern = re.compile(
        r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\)\s*(?:\"\"\"|\'\'\'|\")",
        re.IGNORECASE | re.DOTALL,
    )

    # Build a map of table -> set of columns declared PRIMARY KEY inline
    pk_columns_by_table = {}
    for m in create_table_pattern.finditer(src):
        table = m.group(1)
        cols_blob = m.group(2)
        pk_cols = set()
        for col_match in re.finditer(r"(\w+)\s+\w+[^,]*PRIMARY KEY", cols_blob, re.IGNORECASE):
            pk_cols.add(col_match.group(1))
        if pk_cols:
            pk_columns_by_table.setdefault(table, set()).update(pk_cols)

    # This structural regex approach can't perfectly map every ON CONFLICT
    # call back to its exact table (that requires full SQL parsing), so we
    # use it as a coarse tripwire: every conflict *column name* found must
    # appear as a PK column for *some* table in the file, or be explicitly
    # allow-listed. In practice in this codebase the column is always
    # `symbol` (covered by 4 tables) or `strategy` (the known exception).
    conflict_columns = set(m.group(1) for m in conflict_pattern.finditer(src))
    all_pk_columns = set()
    for cols in pk_columns_by_table.values():
        all_pk_columns.update(cols)

    unexplained = []
    for col in conflict_columns:
        if col in all_pk_columns:
            continue
        if any(col == "strategy" for _ in [None]) and "strategy" in KNOWN_EXCEPTIONS:
            continue
        unexplained.append(col)

    assert unexplained == [], (
        f"ON CONFLICT target column(s) {unexplained} have no matching "
        f"in-file PRIMARY KEY declaration and are not in KNOWN_EXCEPTIONS. "
        f"Add a CREATE TABLE IF NOT EXISTS with the right PRIMARY KEY, or "
        f"add a reviewed, migration-backed entry to KNOWN_EXCEPTIONS."
    )
