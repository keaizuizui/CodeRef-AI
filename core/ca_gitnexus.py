# -*- coding: utf-8 -*-
"""
code_analyzer 拆解子模块：GitNexus 图谱增强
原 core/code_analyzer.py 904~1018 行（模块级辅助函数）。
单向依赖：ca_parse ← ca_gitnexus ← 主文件；类型注解使用字符串引用避免循环 import。
"""

import os
import subprocess
from typing import TYPE_CHECKING, List, Optional

from loguru import logger

if TYPE_CHECKING:
    # 仅供类型标注（pyflakes 解析注解名），运行时零依赖，避免与主文件循环 import
    from core.code_analyzer import ProjectAnalysis


def _enhance_analysis_with_gitnexus(analysis: 'ProjectAnalysis'):
    """用GitNexus图谱数据增强分析结果（原 CodeAnalyzer._enhance_with_gitnexus 实现）
    
    通过MCP查询GitNexus图数据库，获取：
    - 执行流（process）信息
    - 函数集群（community）信息
    - 入口点的上下游关系
    """
    from .gitnexus_client import GitNexusMCPClient
    
    logger.info(f"[GitNexus] 尝试增强分析，项目路径: {analysis.project_path}")
    
    with GitNexusMCPClient(project_path=analysis.project_path) as client:
        # 1. 获取已索引的仓库列表
        repos = client.list_repos()
        logger.info(f"[GitNexus] 发现 {len(repos)} 个索引仓库: {[r.get('name') for r in repos]}")
        if not repos:
            logger.info("[GitNexus] 当前项目无索引数据，跳过增强")
            return
        
        # 2. 搜索项目中的关键符号，补充进程/集群信息
        # 查找入口点文件
        entry_files = []
        for f in analysis.files:
            basename = os.path.basename(f.file_path)
            if basename in ('main.py', 'index.js', 'app.py', 'server.py', 'v4_main.py'):
                entry_files.append(f)
        
        # 3. 对每个入口函数查询上下文
        for ef in entry_files:
            for func in ef.functions:
                if func.name in ('main', 'run', 'start', 'serve', 'app'):
                    try:
                        context = client.get_context(func.name)
                        if isinstance(context, dict):
                            # 补充进程信息到架构摘要
                            processes = context.get("processes", [])
                            if processes:
                                proc_names = []
                                for p in processes[:5]:
                                    if isinstance(p, dict):
                                        proc_names.append(p.get("name", str(p)))
                                    else:
                                        proc_names.append(str(p))
                                if proc_names:
                                    analysis.architecture_summary += (
                                        f"\n[GitNexus] 检测到执行流: {', '.join(proc_names)}"
                                    )
                    except Exception as e:
                        logger.warning(f"应用 GitNexus 执行流增强失败，跳过: {e}")
        
        # 4. 用混合搜索发现项目中的关键模块
        try:
            project_name = os.path.basename(analysis.project_path.rstrip('/\\'))
            search_results = client.search(project_name)
            if isinstance(search_results, dict):
                clusters = search_results.get("clusters", [])
                if clusters:
                    cluster_info = []
                    for c in clusters[:10]:
                        if isinstance(c, dict):
                            name = c.get("name", "")
                            cohesion = c.get("cohesion", "")
                            if name:
                                cluster_info.append(f"{name}(内聚度:{cohesion})" if cohesion else name)
                    if cluster_info:
                        analysis.architecture_summary += (
                            f"\n[GitNexus] 功能集群: {', '.join(cluster_info)}"
                        )
        except Exception as e:
            logger.warning(f"应用 GitNexus 功能集群增强失败，跳过: {e}")
        
        logger.info("[CodeAnalyzer] GitNexus增强完成")


def _run_gitnexus_cmd(cmd: List[str], project_path: str) -> Optional[str]:
    """执行 gitnexus 命令行并返回 stdout（列表式参数，无 shell 注入面）
    （原 CodeAnalyzer._run_gitnexus 实现）

    Args:
        cmd: gitnexus 子命令参数列表（如 ["export", "--entry", "main", ...]）
        project_path: GitNexus 索引所在的项目目录

    Returns:
        命令 stdout 字符串；失败返回 None
    """
    try:
        argv = list(cmd)
        if not argv or argv[0] != "gitnexus":
            argv = ["gitnexus"] + argv
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=project_path,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            logger.warning(f"[CodeAnalyzer] gitnexus 命令失败: {result.stderr.strip()[:200]}")
            return None
        return result.stdout
    except FileNotFoundError:
        logger.warning("[CodeAnalyzer] gitnexus CLI 未安装，请执行: npm install -g gitnexus")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("[CodeAnalyzer] gitnexus 命令执行超时（120s）")
        return None
    except Exception as e:
        logger.warning(f"[CodeAnalyzer] gitnexus 调用异常: {e}")
        return None
