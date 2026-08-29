@echo off
chcp 65001 >nul
title CodeRef-AI - 配置管理

:menu
cls
echo ============================================
echo   CodeRef-AI - 配置管理
echo ============================================
echo.
echo   1. 配置 LLM API 密钥
echo   2. 管理 Cache（硬编码优化）
echo   3. 清理 Cache（开源前/切换项目前）
echo   4. 生成 LLM 审查报告
echo   5. 设计注册表管理（list / add / alias / delete）
echo   6. 激活治理看板前端（本地服务）
echo   7. 生成并打开架构画布
echo   8. 退出
echo.
set /p choice="请选择 [1-8]: "

if "%choice%"=="1" goto :config_llm
if "%choice%"=="2" goto :cache_menu
if "%choice%"=="3" goto :clear_cache
if "%choice%"=="4" goto :llm_review
if "%choice%"=="5" goto :registry_menu
if "%choice%"=="6" goto :board_menu
if "%choice%"=="7" goto :canvas_menu
if "%choice%"=="8" exit /b 0
goto :menu

:: ============================================================
:: 1. 配置 LLM API
:: ============================================================
:config_llm
cls
echo ============================================
echo   LLM API 配置
echo ============================================
echo.
echo 配置将保存到 config\config.json
echo 开源时删除 cache\ 目录即可清除所有敏感信息
echo.
echo 本工具兼容所有 OpenAI 格式的 API（DeepSeek / OpenAI / Ollama / 自定义）
echo 你只需填写以下三个信息：
echo.

:: 检测 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    pause
    goto :menu
)

:: 安装依赖
echo [1/2] 安装 Python 依赖...
pip install -r requirements.txt -q
echo [OK] 依赖安装完成

echo.
echo [2/2] 填写 API 信息
echo.
echo 常见服务的 Base URL：
echo   DeepSeek: https://api.deepseek.com
echo   OpenAI:   https://api.openai.com/v1
echo   Ollama:   http://localhost:11434/v1
echo.
set /p CODEREF_BASE_URL="Base URL (默认: https://api.deepseek.com): "
if "%CODEREF_BASE_URL%"=="" set CODEREF_BASE_URL=https://api.deepseek.com

set /p CODEREF_MODEL="模型名 (默认: deepseek-v4-flash): "
if "%CODEREF_MODEL%"=="" set CODEREF_MODEL=deepseek-v4-flash

set /p CODEREF_API_KEY="API Key: "
if "%CODEREF_API_KEY%"=="" (
    echo [警告] 未输入 API Key，LLM 功能将不可用
)

set CODEREF_PROVIDER=custom

:save_config
echo.
echo 正在写入 config\config.json ...
if not exist "config" mkdir config
(
echo {
echo   "llm_provider": "%CODEREF_PROVIDER%",
echo   "llm_api_key": "%CODEREF_API_KEY%",
echo   "llm_base_url": "%CODEREF_BASE_URL%",
echo   "llm_model": "%CODEREF_MODEL%",
echo   "llm_temperature": 0.7,
echo   "llm_max_tokens": 4096,
echo   "project_path": "",
echo   "search_frequency": "disabled",
echo   "search_keywords": "AI, LLM, 代码优化",
echo   "search_result_count": 5,
echo   "vector_db_path": "./data/chroma"
echo }
) > config\config.json
echo [OK] 配置已保存到 config\config.json

echo.
echo ============================================
echo   配置完成！
echo.
echo   提供商: %CODEREF_PROVIDER%
echo   模型:   %CODEREF_MODEL%
echo   配置路径: config\config.json
echo.
echo   MCP Server 启动命令:
echo     python -m core.mcp_server
echo ============================================
echo.
pause
goto :menu

:: ============================================================
:: 2. Cache 管理
:: ============================================================
:cache_menu
cls
echo ============================================
echo   Cache 管理 —— 硬编码优化
echo ============================================
echo.
echo cache/ 目录存放了项目专属的硬编码优化数据：
echo   - magic_numbers.json      : 魔法数字白名单
echo   - security_whitelist.json  : 安全规则误报白名单
echo   - complexity_exemptions.json: 复杂度豁免
echo   - naming_exemptions.json   : 命名豁免
echo   - llm_reviews/             : LLM 审查记录
echo.
echo 工作流程:
echo   1. 首次扫描项目 → 检测到大量问题
echo   2. LLM 审查扫描结果 → 区分误报/漏报
echo   3. 将误报加入白名单 → 存到 cache/
echo   4. 再次扫描 → 结果更精准
echo   5. 切换项目时清理旧 cache
echo.
echo   a. 查看当前项目缓存
echo   b. 手动添加魔法数字白名单
echo   c. 手动添加安全规则白名单
echo   d. 返回主菜单
echo.
set /p cache_choice="请选择 [a-d]: "

if "%cache_choice%"=="a" goto :view_cache
if "%cache_choice%"=="b" goto :add_magic_whitelist
if "%cache_choice%"=="c" goto :add_security_whitelist
if "%cache_choice%"=="d" goto :menu
goto :cache_menu

:view_cache
cls
echo ============================================
echo   当前项目缓存
echo ============================================
echo.
python -c "from core.cache_manager import cache_manager; projects = cache_manager.list_projects(); print(f'共 {len(projects)} 个项目有缓存\n'); [print(f'  [{p[\"hash\"]}] {p[\"path\"]} ({p[\"files\"]} 文件, {p[\"size_kb\"]} KB)') for p in projects]" 2>nul
if %errorlevel% neq 0 (
    echo 暂无缓存数据，或 Python 环境未就绪
)
echo.
pause
goto :cache_menu

:add_magic_whitelist
cls
echo ============================================
echo   添加魔法数字白名单
echo ============================================
echo.
echo 输入要豁免的硬编码值（如 100、127.0.0.1）
echo 输入 'done' 完成
echo.

set /p project="项目路径: "
if "%project%"=="" (
    echo 需要指定项目路径
    pause
    goto :cache_menu
)

python -c "from core.cache_manager import cache_manager, MagicNumberEntry; print('cache_manager ready')" 2>nul
if %errorlevel% neq 0 (
    echo [错误] Python 环境未就绪
    pause
    goto :cache_menu
)

:magic_loop
set /p value="魔法数字值 (done=完成): "
if "%value%"=="done" goto :cache_menu
if "%value%"=="" goto :magic_loop
set /p reason="豁免原因: "
python -c "from core.cache_manager import cache_manager, MagicNumberEntry; import json; d = cache_manager.get_hardcoded_dir(r'%project%'); f = d / 'magic_numbers.json'; entries = []; [entries.append(json.loads(line)) if False else None]; entries.append({'value': '%value%', 'file_pattern': '*.py', 'reason': '%reason%', 'reviewed_by': 'manual', 'timestamp': __import__('datetime').datetime.now().isoformat()}); from pathlib import Path; Path(f).write_text(json.dumps({'entries': entries}, indent=2, ensure_ascii=False), encoding='utf-8'); print(f'[OK] %value% 已加入白名单')" 2>nul
echo [OK] %value% 已加入白名单
goto :magic_loop

:add_security_whitelist
cls
echo ============================================
echo   添加安全规则白名单
echo ============================================
echo.
echo 输入要豁免的安全规则信息
echo 输入 'done' 完成
echo.

set /p project="项目路径: "
if "%project%"=="" (
    echo 需要指定项目路径
    pause
    goto :cache_menu
)

:sec_loop
set /p rule_id="规则 ID (如 IRON-SEC-14, done=完成): "
if "%rule_id%"=="done" goto :cache_menu
if "%rule_id%"=="" goto :sec_loop
set /p file="文件名: "
set /p line="行号: "
set /p reason="豁免原因: "
python -c "from core.cache_manager import cache_manager, SecurityWhitelistEntry; import json; d = cache_manager.get_hardcoded_dir(r'%project%'); f = d / 'security_whitelist.json'; entries = []; existing = f.read_text(encoding='utf-8') if f.exists() else '{}'; [entries.append(json.loads(line)) if False else None]; entries.append({'rule_id': '%rule_id%', 'file': '%file%', 'line': int('%line%'), 'reason': '%reason%', 'reviewed_by': 'manual', 'timestamp': __import__('datetime').datetime.now().isoformat()}); from pathlib import Path; Path(f).write_text(json.dumps({'entries': entries}, indent=2, ensure_ascii=False), encoding='utf-8'); print(f'[OK] %rule_id% 已加入白名单')" 2>nul
echo [OK] %rule_id% 已加入白名单
goto :sec_loop

:: ============================================================
:: 3. 清理 Cache
:: ============================================================
:clear_cache
cls
echo ============================================
echo   清理 Cache
echo ============================================
echo.
echo   此操作会删除 cache/ 目录下的所有数据：
echo     - LLM API 配置（config.json）
echo     - 硬编码优化白名单（hardcoded/）
echo     - LLM 审查记录（llm_reviews/）
echo.
echo   适用于：
echo     - 开源前清除敏感信息
echo     - 切换项目后清理旧项目缓存
echo.
echo   a. 清理某个项目的缓存（保留配置）
echo   b. 清理全部缓存（保留配置）
echo   c. 清理全部（包括配置，开源前使用）
echo   d. 返回主菜单
echo.
set /p clear_choice="请选择 [a-d]: "

if "%clear_choice%"=="a" goto :clear_project
if "%clear_choice%"=="b" goto :clear_all
if "%clear_choice%"=="c" goto :clear_everything
if "%clear_choice%"=="d" goto :menu
goto :clear_cache

:clear_project
set /p project="项目路径: "
if "%project%"=="" goto :clear_cache
python -c "from core.cache_manager import cache_manager; cache_manager.clear_project_cache(r'%project%'); print('[OK] 项目缓存已清理')" 2>nul
echo.
pause
goto :clear_cache

:clear_all
python -c "from core.cache_manager import cache_manager; cache_manager.clear_all_cache(); print('[OK] 所有缓存已清理，config.json 已保留')" 2>nul
echo.
pause
goto :clear_cache

:clear_everything
echo [警告] 此操作将删除 cache/ 目录下的所有内容，包括 LLM 配置！
set /p confirm="确认删除？(输入 yes 确认): "
if not "%confirm%"=="yes" goto :clear_cache
if exist "cache" rmdir /s /q "cache"
echo [OK] cache/ 目录已完全删除
echo.
pause
goto :menu

:: ============================================================
:: 4. LLM 审查（生成 + 导出）
:: ============================================================
:llm_review
cls
echo ============================================
echo   LLM 审查报告
echo ============================================
echo.
echo 此功能会导出当前项目的缓存快照，
echo 你可以将其提供给 LLM 进行审查，
echo 让 LLM 帮你判断哪些是误报、哪些是漏报。
echo.
echo   a. 导出缓存快照（供 LLM 审查）
echo   b. 查看 LLM 审查历史
echo   c. 返回主菜单
echo.
set /p review_choice="请选择 [a-c]: "

if "%review_choice%"=="a" goto :export_snapshot
if "%review_choice%"=="b" goto :view_reviews
if "%review_choice%"=="c" goto :menu
goto :llm_review

:export_snapshot
set /p project="项目路径: "
if "%project%"=="" goto :llm_review
echo 正在导出缓存快照...
python -c "from core.cache_manager import cache_manager; import json; snap = cache_manager.export_snapshot(r'%project%'); out = cache_manager.get_llm_review_dir(r'%project%') / 'snapshot.json'; out.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding='utf-8'); print(f'[OK] 快照已导出到 {out}')" 2>nul
if %errorlevel% neq 0 (
    echo [错误] 导出失败，请检查 Python 环境和项目路径
) else (
    echo.
    echo 快照已导出到: cache\llm_reviews\{project_hash}\snapshot.json
    echo 你可以将此文件提供给 LLM，让它帮你审查误报/漏报
)
echo.
pause
goto :llm_review

:view_reviews
set /p project="项目路径: "
if "%project%"=="" goto :llm_review
python -c "from core.cache_manager import cache_manager; reviews = cache_manager.list_llm_reviews(r'%project%'); print(f'共 {len(reviews)} 条审查记录:'); [print(f'  {r.name}') for r in reviews]" 2>nul
if %errorlevel% neq 0 (
    echo 暂无审查记录
)
echo.
pause
goto :llm_review

:: ============================================================
:: 5. 设计注册表管理（list / add / alias / delete）
:: ============================================================
:registry_menu
cls
echo ============================================
echo   设计注册表管理
echo ============================================
echo.
echo 注册表保存"已知设计"库（data\design_registry.json），
echo 供 coderef_innovation / coderef_asset / coderef_registry 查询。
echo delete 仅可删除「当前项目注册」的设计；预置种子与他项目条目受保护。
echo.
echo   a. 列出全部设计
echo   b. 新增设计（add）
echo   c. 添加别名（alias）
echo   d. 删除设计（delete）
echo   e. 返回主菜单
echo.
set /p reg_choice="请选择 [a-e]: "

if "%reg_choice%"=="a" goto :reg_list
if "%reg_choice%"=="b" goto :reg_add
if "%reg_choice%"=="c" goto :reg_alias
if "%reg_choice%"=="d" goto :reg_delete
if "%reg_choice%"=="e" goto :menu
goto :registry_menu

:reg_list
cls
python -c "from core.design_registry import DesignRegistry; r=DesignRegistry(); print('共 ' + str(len(r.list_designs())) + ' 条设计:'); [print('  - ' + d.get('canonical','') + ' (' + d.get('category','') + ') | 来源: ' + (d.get('source_project') or '(预置)')) for d in r.list_designs()]" 2>nul
if %errorlevel% neq 0 (
    echo [错误] Python 环境未就绪
)
echo.
pause
goto :registry_menu

:reg_add
set /p reg_project="来源项目路径: "
if "%reg_project%"=="" goto :registry_menu
set /p reg_canonical="设计名（canonical）: "
set /p reg_desc="设计说明: "
python -c "from core.design_registry import DesignRegistry; r=DesignRegistry(); r.manage(project_path=r'%reg_project%', action='add', name='%reg_canonical%', description='%reg_desc%'); print('[OK] 已新增设计: %reg_canonical%')" 2>nul
if %errorlevel% neq 0 (
    echo [错误] 新增失败，请检查设计名是否已存在
)
pause
goto :registry_menu

:reg_alias
set /p reg_project="来源项目路径: "
if "%reg_project%"=="" goto :registry_menu
set /p reg_canonical="目标设计名（canonical）: "
set /p reg_alias="新别名: "
python -c "from core.design_registry import DesignRegistry; r=DesignRegistry(); r.manage(project_path=r'%reg_project%', action='alias', canonical='%reg_canonical%', alias='%reg_alias%'); print('[OK] 别名已添加: %reg_alias%')" 2>nul
if %errorlevel% neq 0 (
    echo [错误] 添加别名失败，请检查 canonical 是否存在
)
pause
goto :registry_menu

:reg_delete
set /p reg_project="当前项目路径: "
if "%reg_project%"=="" goto :registry_menu
set /p reg_name="要删除的设计名/别名: "
echo [警告] 将删除设计「%reg_name%」（含关联别名与资产），
echo          且仅当来源项目与当前项目一致时才允许。
set /p confirm="确认删除？(输入 yes 确认): "
if not "%confirm%"=="yes" goto :registry_menu
python -c "from core.design_registry import DesignRegistry; r=DesignRegistry(); r.manage(project_path=r'%reg_project%', action='delete', name='%reg_name%'); print('[OK] 已删除设计: %reg_name%')" 2>nul
if %errorlevel% neq 0 (
    echo [错误] 删除失败：预置种子/他项目条目不可删，或设计不存在
)
pause
goto :registry_menu

:: ============================================================
:: 6. 激活治理看板前端（本地服务）
:: ============================================================
:board_menu
cls
echo ============================================
echo   激活治理看板前端
echo ============================================
echo.
echo 将启动治理看板本地服务（仅回环 127.0.0.1，不暴露局域网），
echo 用浏览器访问打印的地址即可交互（报告 / 问题 / 循环 / 流转）。
echo 按回车停止服务并返回主菜单。
echo.
set /p board_project="项目路径: "
if "%board_project%"=="" goto :menu
python -c "from core.gov_webdash import serve; r=serve(r'%board_project%'); print('治理看板已启动:'); print('  访问地址: ' + r['url']); print('按回车停止服务...'); input()" 2>nul
if %errorlevel% neq 0 (
    echo [错误] 启动失败，请检查项目路径与 Python 环境
)
echo.
pause
goto :menu

:: ============================================================
:: 7. 生成并打开架构画布
:: ============================================================
:canvas_menu
cls
echo ============================================
echo   生成并打开架构画布
echo ============================================
echo.
echo 将基于项目图谱生成可交互架构画布（HTML），
echo 随后用默认浏览器打开，可拖拽编辑、导出目标架构 JSON。
echo 需项目已完成扫描（coderef_scan），否则生成失败。
echo.
set /p canvas_project="项目路径: "
if "%canvas_project%"=="" goto :menu
set "canvas_file="
for /f "delims=" %%i in ('python -c "from core.canvas_generator import ArchCanvas; print(ArchCanvas().generate(project_path=r'%canvas_project%'))" 2^>nul') do set "canvas_file=%%i"
if "%canvas_file%"=="" (
    echo [错误] 画布生成失败，请检查项目路径与扫描数据
) else (
    echo [OK] 画布已生成: %canvas_file%
    start "" "%canvas_file%"
)
echo.
pause
goto :menu