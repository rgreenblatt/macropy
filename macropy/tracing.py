# -*- coding: utf-8 -*-
import ast
import copy
import sys
from itertools import count
from typing import Optional, Set, Dict

import macropy.core
import macropy.core.macros
import macropy.core.walkers

from macropy.core.quotes import ast_literal, u
from macropy.core.hquotes import macros, hq, unhygienic


macros = macropy.core.macros.Macros()  # noqa: F811


def literal_eval(node_or_string):
    """
    Safely evaluate an expression node or a string containing a Python
    expression.  The string or node provided may only consist of the
    following Python literal structures: strings, numbers, tuples,
    lists, dicts, booleans, and None.
    """
    _safe_names = {'None': None, 'True': True, 'False': False}
    if isinstance(node_or_string, str):
        node_or_string = ast.parse(node_or_string, mode='eval')
    if isinstance(node_or_string, ast.Expression):
        node_or_string = node_or_string.body

    def _convert(node):
        if isinstance(node, ast.Str):
            return node.s
        elif isinstance(node, ast.Num):
            return node.n
        elif isinstance(node, ast.Tuple):
            return tuple(map(_convert, node.elts))
        elif isinstance(node, ast.List):
            return list(map(_convert, node.elts))
        elif isinstance(node, ast.Dict):
            return dict((_convert(k), _convert(v)) for k, v
                        in zip(node.keys, node.values))
        elif isinstance(node, ast.Name):
            if node.id in _safe_names:
                return _safe_names[node.id]
        elif (isinstance(node, ast.BinOp) and
              isinstance(node.op, (ast.Add, ast.Sub)) and
              isinstance(node.right, ast.Num) and
              isinstance(node.right.n, complex) and
              isinstance(node.left, ast.Num) and
              isinstance(node.left.n, (int, float))):  # TODO: long,
            left = node.left.n
            right = node.right.n
            if isinstance(node.op, ast.Add):
                return left + right
            else:
                return left - right
        raise ValueError('malformed string')
    return _convert(node_or_string)

def custom_repr(x):
    if isinstance(x, float):
        return f"{x:.4f}".rstrip('0').rstrip('.')
    return repr(x)

def wrap(printer, txt, x, **printer_kwargs):
    string = txt + " -> " + custom_repr(x)
    printer(string, **printer_kwargs)
    return x


def wrap_simple(printer, txt, x):
    string = txt
    printer(string)
    return x


@macros.expr
def log(tree, exact_src, **kw):
    """Prints out source code of the wrapped expression and the value it
    evaluates to"""
    new_tree = hq[wrap(unhygienic[log], u[exact_src(tree)], ast_literal[tree])]
    yield new_tree


@macros.expr
def show_expanded(tree, expand_macros,  **kw):
    """Prints out the expanded version of the wrapped source code, after all
    macros inside it have been expanded"""
    new_tree = hq[wrap_simple(
        unhygienic[log], u[macropy.core.unparse(tree)],
        ast_literal[tree])]
    return new_tree


@macros.block   # noqa: F811
def show_expanded(tree, expand_macros, **kw):
    """Prints out the expanded version of the wrapped source code, after all
    macros inside it have been expanded"""
    new_tree = []
    for stmt in tree:
        with hq as code:
            unhygienic[log](u[macropy.core.unparse(stmt)])
        new_tree.append(code)
        new_tree.append(stmt)

    return new_tree


def trace_walk_func(tree, exact_src, log_kwargs: Dict = {}, **kw):
    @macropy.core.walkers.Walker
    def trace_walk(tree, stop, log_kwargs: Dict = {}, **kw):
        is_del_or_store = isinstance(tree, ast.Subscript) and isinstance(tree.ctx, (ast.Store, ast.Del))
        is_attribute_assign = isinstance(tree, ast.Attribute) and isinstance(tree.ctx, (ast.Store, ast.Del))

        if (isinstance(tree, ast.expr) and
            tree._fields != () and
            type(tree) is not ast.Name and
            not is_del_or_store and
            not is_attribute_assign):  # noqa: E129

            try:
                literal_eval(tree)
                stop()
                return tree
            except ValueError as e:
                txt = exact_src(tree)
                trace_walk.walk_children(tree, log_kwargs=log_kwargs)
                wrapped = hq[
                    wrap(unhygienic[log], u[txt], ast_literal[tree], **log_kwargs)
                ]
                assert isinstance(wrapped.args[0], ast.Name)
                wrapped.args[0].ctx = ast.Load()
                stop()
                return wrapped

        elif isinstance(tree, ast.stmt):
            txt = exact_src(tree)
            trace_walk.walk_children(
                tree, skip_assign_target_if_tuple=True, log_kwargs=log_kwargs
            )
            with hq as code:
                unhygienic[log](u[txt], **log_kwargs)
            stop()
            return [code, tree]
        elif is_del_or_store:
            assert isinstance(tree, ast.Subscript)
            if isinstance(tree.slice, ast.Index):
                tree.slice.value = trace_walk.recurse(
                    tree.slice.value, log_kwargs=log_kwargs
                )
            else:
                assert isinstance(tree.slice, ast.Slice)
                if tree.slice.lower is not None:
                    tree.slice.lower = trace_walk.recurse(tree.slice.lower)
                if tree.slice.upper is not None:
                    tree.slice.upper = trace_walk.recurse(tree.slice.upper)
                if tree.slice.step is not None:
                    tree.slice.step = trace_walk.recurse(tree.slice.step)
            stop()
            return tree
        elif is_attribute_assign:
            assert isinstance(tree, ast.Attribute)
            tree.value = trace_walk.recurse(tree.value)
            stop()
            return tree

    return trace_walk.recurse(tree, log_kwargs=log_kwargs)


@macros.expr
def trace(tree, exact_src, **kw):
    """Traces the wrapped code, printing out the source code and evaluated
    result of every statement and expression contained within it"""
    ret = trace_walk_func(tree, exact_src)
    yield ret


@macros.block  # noqa: F811
def trace(tree, exact_src, **kw):
    """Traces the wrapped code, printing out the source code and evaluated
    result of every statement and expression contained within it"""
    ret = trace_walk_func(tree, exact_src)
    yield ret

line_cache = set()

@macros.block  # noqa: F811
def trace_set(tree, exact_src, **kw):
    """Traces the wrapped code, printing out the source code and evaluated
    result of every statement and expression contained within it"""
    ret = trace_walk_func(
        tree,
        exact_src,
        log_kwargs=dict(
            use_stack_depth=True,
            stack_depth_sub=7,
            line_cache=line_cache,
            max_rep_line_len=40,
            use_bullet=True,
            skip_builtins=True,
            skip_same=True,
        ),
    )
    yield ret


def require_transform(tree, exact_src):
    ret = trace_walk_func(copy.deepcopy(tree), exact_src)
    trace_walk_func(copy.deepcopy(tree), exact_src)
    new = hq[ast_literal[tree] or wrap_require(lambda log: ast_literal[ret])]
    return new


def wrap_require(thunk):
    out = []
    thunk(out.append)
    raise AssertionError("Require Failed\n" + "\n".join(out))


@macros.expr
def require(tree, exact_src, **kw):
    """A version of assert that traces the expression's evaluation in the
    case of failure. If used as a block, performs this on every expression
    within the block"""
    yield require_transform(tree, exact_src)


@macros.block  # noqa: F811
def require(tree, exact_src, **kw):
    """A version of assert that traces the expression's evaluation in the
    case of failure. If used as a block, performs this on every expression
    within the block"""
    for expr in tree:
        expr.value = require_transform(expr.value, exact_src)

    yield tree


def stack_size4b(size_hint=8):
    """Get stack size for caller's frame."""
    get_frame = sys._getframe
    frame = None
    try:
        while True:
            frame = get_frame(size_hint)
            size_hint *= 2
    except ValueError:
        if frame:
            size_hint //= 2
        else:
            while not frame:
                size_hint = max(2, size_hint // 2)
                try:
                    frame = get_frame(size_hint)
                except ValueError:
                    continue

    for size in count(size_hint):
        frame = frame.f_back
        if not frame:
            return size


@macros.expose_unhygienic  # noqa: F811
def log(
    x,
    use_stack_depth: bool = False,
    stack_depth_sub: int = 7,
    line_cache: Optional[Set[str]] = None,
    max_rep_line_len: int = 40,
    use_bullet: bool = False,
    skip_builtins: bool = False,
    skip_same: bool = False,
):
    if use_stack_depth:
        stack_depth = stack_size4b()
        assert stack_depth is not None
        stack_depth_sub = stack_depth - 7
    else:
        stack_depth_sub = 0
    split = str(x).splitlines()

    def truncate_line(x: str):
        if len(x) > max_rep_line_len and "->" not in x:
            return x[:max_rep_line_len] + "…"
        return x

    # hack
    if skip_builtins and len(split) == 1 and "-> <built-in " in split[0]:
        return

    if skip_same and len(split) == 1 and " -> " in split[0] and  split[0].partition(" -> ")[0] == split[0].partition(" -> ")[-1]:
        return

    if line_cache is not None:
        if split[0] in line_cache:
            assert all(sp in line_cache for sp in split)
            split = [truncate_line(sp) for sp in split]
            if len(split) > 2:
                split = [split[0], "…", split[-1]]
        else:
            for sp in split:
                line_cache.add(sp)

    prefix = "• " if use_bullet else ""

    s = "\n".join(("    " * stack_depth_sub) + prefix + l for l in split)
    print(s)
