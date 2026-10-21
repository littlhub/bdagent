"""Python 源码结构解析：提取函数定义、函数内调用与导入模块。"""

import ast


def _called_names(node):
    names = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return names


def _import_names(node):
    names = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            names.append(alias.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            names.append(node.module.split(".")[0])
        else:
            names.append("." * (node.level or 1))
    return names


def parse_py_file(path, rel):
    """返回 {rel: {functions, imports}} 中的结构体；解析失败返回 None。"""
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    return parse_py_source(source, rel)


def parse_py_source(source, rel):
    """解析已读入的源码文本；语法错误时返回 None。

    与 parse_py_file 相同，但省掉一次磁盘读取——范围定位阶段
    已经把内容读进内存，可直接复用。
    """
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError):
        return None

    functions = {}
    imports = []
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = _called_names(node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.extend(_import_names(node))

    seen = set()
    unique_imports = []
    for name in imports:
        if name not in seen:
            seen.add(name)
            unique_imports.append(name)

    return {
        "rel": rel,
        "functions": functions,
        "imports": unique_imports,
    }
