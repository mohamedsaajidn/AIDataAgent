"""Static safety check on generated code before it ever reaches the container.

This is a second layer, not the primary boundary - the container's network
isolation, read-only filesystem, and resource limits are what actually make
execution safe. This catches the obvious/lazy attempts and fails fast with a
clear message instead of relying solely on runtime isolation.
"""

import ast
import posixpath

_BANNED_IMPORTS = {"subprocess", "socket", "ctypes", "multiprocessing", "pty", "importlib", "shutil"}
_BANNED_OS_ATTRS = {
    "system", "popen", "fork", "kill", "remove", "unlink", "rmdir", "rename", "replace",
    "chmod", "chown",
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
}
_BANNED_CALL_NAMES = {"eval", "exec", "__import__", "compile"}
_ALLOWED_OPEN_PREFIXES = ("/scratch", "/data")


def check_code(code: str) -> list[str]:
    """Return a list of violation messages; empty means the code passed."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"code does not parse: {exc}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(_check_import(node))
        elif isinstance(node, ast.ImportFrom):
            violations.extend(_check_import_from(node))
        elif isinstance(node, ast.Call):
            violations.extend(_check_call(node))
    return violations


def _check_import(node: ast.Import) -> list[str]:
    return [
        f"import of '{alias.name}' is not allowed"
        for alias in node.names
        if alias.name.split(".")[0] in _BANNED_IMPORTS
    ]


def _check_import_from(node: ast.ImportFrom) -> list[str]:
    if node.module and node.module.split(".")[0] in _BANNED_IMPORTS:
        return [f"import from '{node.module}' is not allowed"]
    return []


def _check_call(node: ast.Call) -> list[str]:
    func = node.func

    if isinstance(func, ast.Name):
        if func.id in _BANNED_CALL_NAMES:
            return [f"call to '{func.id}' is not allowed"]
        if func.id == "open":
            return _check_open_call(node)

    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "os" and func.attr in _BANNED_OS_ATTRS:
            return [f"call to 'os.{func.attr}' is not allowed"]

    return []


def _check_open_call(node: ast.Call) -> list[str]:
    if not node.args:
        return []
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        path = first.value
        if not path.startswith("/"):
            return []
        # normpath collapses "/scratch/../etc/shadow" to "/etc/shadow" before
        # the check - a naive string prefix check is fooled both by ".." and
        # by a sibling like "/scratchevil" that merely starts with the same
        # characters without being inside the directory.
        normalized = posixpath.normpath(path)
        if not any(normalized == p or normalized.startswith(p + "/") for p in _ALLOWED_OPEN_PREFIXES):
            return [f"open() of absolute path '{path}' outside /data or /scratch is not allowed"]
    return []
