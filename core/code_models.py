# -*- coding: utf-8 -*-
"""
共享数据模型 —— 从 code_analyzer 抽取，切断 code_analyzer ↔ ast_parser 循环依赖

背景：code_analyzer 需要 ast_parser 做精确解析，ast_parser 又反向依赖
code_analyzer 的数据模型（CodeFile/CodeFunction/CodeClass），两者靠函数内
惰性导入规避顶层循环崩溃。本模块把纯数据模型抽到只依赖标准库的独立位置，
两个解析器都只依赖本模块，反向边被切断。

设计原则：
- 零依赖：只依赖 Python 标准库（dataclasses / typing / collections），
  不 import 任何 core 内部模块，确保不会引入新的循环。
- 纯数据结构：不含解析/分析逻辑，to_dict/from_dict 仅做序列化。
"""

from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field


@dataclass
class CodeFunction:
    """函数/方法信息"""
    name: str
    start_line: int
    end_line: int
    parameters: List[str] = field(default_factory=list)
    return_type: Optional[str] = None
    docstring: Optional[str] = None
    code: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "start_line": self.start_line,
            "end_line": self.end_line, "parameters": self.parameters,
            "return_type": self.return_type, "docstring": self.docstring,
            "code": self.code
        }

    @staticmethod
    def from_dict(d: dict) -> 'CodeFunction':
        return CodeFunction(**{k: d[k] for k in CodeFunction.__dataclass_fields__ if k in d})


@dataclass
class CodeClass:
    """类信息"""
    name: str
    start_line: int
    end_line: int
    methods: List[CodeFunction] = field(default_factory=list)
    base_classes: List[str] = field(default_factory=list)
    docstring: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "start_line": self.start_line,
            "end_line": self.end_line,
            "methods": [m.to_dict() for m in self.methods],
            "base_classes": self.base_classes, "docstring": self.docstring
        }

    @staticmethod
    def from_dict(d: dict) -> 'CodeClass':
        obj = CodeClass(**{k: d[k] for k in ['name', 'start_line', 'end_line', 'base_classes', 'docstring'] if k in d})
        obj.methods = [CodeFunction.from_dict(m) for m in d.get('methods', [])]
        return obj


@dataclass
class CodeFile:
    """代码文件信息"""
    file_path: str
    language: str
    imports: List[str] = field(default_factory=list)
    functions: List[CodeFunction] = field(default_factory=list)
    classes: List[CodeClass] = field(default_factory=list)
    dependencies: Set[str] = field(default_factory=set)
    raw_content: str = ""
    # === 增强分析字段 ===
    project_imports: List[str] = field(default_factory=list)
    sys_path_inserts: List[str] = field(default_factory=list)
    dynamic_imports: List[Dict] = field(default_factory=list)
    http_calls: List[Dict] = field(default_factory=list)
    function_calls: List[str] = field(default_factory=list)
    ast_assignments: List[Any] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path, "language": self.language,
            "imports": self.imports,
            "functions": [f.to_dict() for f in self.functions],
            "classes": [c.to_dict() for c in self.classes],
            "dependencies": list(self.dependencies),
            "raw_content": self.raw_content,  # 完整内容，供后续审计使用
            "project_imports": self.project_imports,
            "sys_path_inserts": self.sys_path_inserts,
            "dynamic_imports": self.dynamic_imports,
            "http_calls": self.http_calls,
            "function_calls": self.function_calls,
            "ast_assignments": self.ast_assignments,
        }

    @staticmethod
    def from_dict(d: dict) -> 'CodeFile':
        obj = CodeFile(file_path=d.get("file_path", ""), language=d.get("language", ""))
        obj.imports = d.get("imports", [])
        obj.functions = [CodeFunction.from_dict(f) for f in d.get("functions", [])]
        obj.classes = [CodeClass.from_dict(c) for c in d.get("classes", [])]
        obj.dependencies = set(d.get("dependencies", []))
        obj.raw_content = d.get("raw_content", "")
        obj.project_imports = d.get("project_imports", [])
        obj.sys_path_inserts = d.get("sys_path_inserts", [])
        obj.dynamic_imports = d.get("dynamic_imports", [])
        obj.http_calls = d.get("http_calls", [])
        obj.function_calls = d.get("function_calls", [])
        obj.ast_assignments = d.get("ast_assignments", [])
        return obj