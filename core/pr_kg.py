# -*- coding: utf-8 -*-
"""
知识图谱查询家族（core/pipeline_runner 拆解子模块）：_kgq_* 8 个 + 分发器 + kg_query 入口。

原为 pipeline_runner.py 128~231 行；拆出后由主文件 re-export，对外符号不变。
依赖方向：本模块仅依赖 pr_models（未直接使用），供主文件与 MCP 层调用。
"""

# ══ 模块级函数（原 Pipe 类零状态方法，v4.9.5 存量债治理提取） ══
def _kg_with_meta(data: dict, meta: dict) -> dict:
    """把图谱元信息（构建时间等）并入查询结果并返回。"""
    data.update(meta)
    return data


def _kg_nodes_result(r, meta: dict) -> dict:
    """nodes/total 型查询结果的统一包装。"""
    return _kg_with_meta(
        {"nodes": [n.to_dict() for n in r.nodes], "total": r.total}, meta)


def _kgq_stats(kg, kwargs, meta):
    return _kg_with_meta(kg.get_stats(), meta)


def _kgq_entity(kg, kwargs, meta):
    r = kg.query_entity(kwargs.get("name", ""), kwargs.get("type"))
    return _kg_nodes_result(r, meta)


def _kgq_callers(kg, kwargs, meta):
    return _kg_nodes_result(kg.query_callers(kwargs.get("func_name", "")), meta)


def _kgq_callees(kg, kwargs, meta):
    return _kg_nodes_result(kg.query_callees(kwargs.get("func_name", "")), meta)


def _kgq_impact(kg, kwargs, meta):
    return _kg_nodes_result(kg.query_impact(kwargs.get("file_path", "")), meta)


def _kgq_relations(kg, kwargs, meta):
    r = kg.query_relations(kwargs.get("node_id", ""))
    return _kg_with_meta({"nodes": [n.to_dict() for n in r.nodes],
                          "edges": [e.to_dict() for e in r.edges], "total": r.total}, meta)


def _kgq_file_entities(kg, kwargs, meta):
    return _kg_nodes_result(kg.query_file_entities(kwargs.get("file_path", "")), meta)


def _kgq_search(kg, kwargs, meta):
    return _kg_nodes_result(
        kg.search(kwargs.get("keyword", ""), kwargs.get("limit", 30)), meta)


def _kgq_call_graph(kg, kwargs, meta):
    r = kg.get_call_graph(kwargs.get("func_name", ""), kwargs.get("depth", 2))
    return _kg_with_meta({"nodes": [n.to_dict() for n in r.nodes],
                          "edges": [e.to_dict() for e in r.edges], "total": r.total}, meta)


_KG_QUERY_HANDLERS = {
    "stats": _kgq_stats,
    "entity": _kgq_entity,
    "callers": _kgq_callers,
    "callees": _kgq_callees,
    "impact": _kgq_impact,
    "relations": _kgq_relations,
    "file_entities": _kgq_file_entities,
    "search": _kgq_search,
    "call_graph": _kgq_call_graph,
}


def _kg_query_dispatch(kg, query_type: str, kwargs: dict, meta: dict) -> dict:
    """按 query_type 查表分发到对应图谱查询。"""
    handler = _KG_QUERY_HANDLERS.get(query_type)
    if handler is None:
        return {"error": f"未知查询类型: {query_type}，支持: stats/entity/callers/callees/impact/relations/file_entities/search/call_graph"}
    return handler(kg, kwargs, meta)


def kg_query(project_path: str, query_type: str, **kwargs) -> dict:
    """查询项目知识图谱。

    query_type:
      - stats: 获取统计信息
      - entity: 按名称搜索实体 (name, type?)
      - callers: 查询调用者 (func_name)
      - callees: 查询被调用者 (func_name)
      - impact: 修改影响分析 (file_path)
      - relations: 查询节点关系 (node_id)
      - file_entities: 查询文件实体 (file_path)
      - search: 全文搜索 (keyword)
      - call_graph: 调用链子图 (func_name, depth?)
    """
    from core.code_knowledge_graph import load_knowledge_graph
    kg = load_knowledge_graph(project_path)
    if not kg:
        return {"error": "知识图谱不存在，请先运行 coderef_audit/coderef_docs/coderef_architecture"}

    # 元信息：图谱构建时间，供调用方识别数据新旧，避免把旧图谱当成本次审计结果
    kg_built_at = kg.get_built_at()
    meta = {"kg_built_at": kg_built_at,
            "kg_note": f"知识图谱构建于 {kg_built_at}，仅当本次运行 coderef_audit/coderef_docs/coderef_architecture 后才会重建；若代码有改动，请先重建图谱再查询。" if kg_built_at else "知识图谱缺少构建时间标记"}

    try:
        return _kg_query_dispatch(kg, query_type, kwargs, meta)
    finally:
        kg.close()
