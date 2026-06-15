---
name: boss-jd-resume-collector
description: 当用户想输入 JD/岗位描述并采集 BOSS 直聘候选人简历用于后续匹配时使用本 skill，例如“输入 JD 找合适简历”、“BOSS 简历采集”、“根据 JD 收集候选人”、“先采集候选人简历”。本 skill 只编排 boss-cli 采集简历，不做 JD/简历匹配、排序或推荐。
---

# BOSS JD 简历采集

## 目标

根据一份 JD，在本机采集 BOSS 直聘简历数据，供后续离线匹配使用。本 skill 只负责环境引导、采集、校验和生成清单；不打分、不排序、不推荐候选人。

## 零环境启动

本 skill 面向“只安装 Codex、没有配置 Python/Node/Git”的 Windows 机器。优先使用 PowerShell 脚本，不要求系统已安装 Python。

> 重要：所有 bootstrap 和采集命令都必须在非 sandbox 的本机用户环境执行，不能在 Codex 沙箱、受限 shell 或容器里跑。它们需要访问网络、读写 `~/.boss-cli` / `%APPDATA%\npm`、解析真实 PATH、启动/复用 BOSS 浏览器会话；沙箱里通常会找不到 `boss.cmd`，或无法启动/复用 BOSS 浏览器。
>
> 如果当前环境解析不到 `boss`，不要改用 PATH 上陌生的 `boss.exe`。先运行 bootstrap，或显式传入 bootstrap 生成的 `-BossBin "$HOME\.boss-cli\bin\boss.cmd"` / `--boss-bin "$HOME\.boss-cli\bin\boss.cmd"`；采集脚本也会优先读取 `~\.boss-cli\toolchain\boss-command.json`。

拿到单个 `boss-jd-resume-collector.zip` 后，先解压到 Codex skills 目录：

```powershell
$skillsDir = Join-Path $HOME ".codex\skills"
New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null
Expand-Archive -LiteralPath ".\boss-jd-resume-collector.zip" -DestinationPath $skillsDir -Force
Test-Path (Join-Path $skillsDir "boss-jd-resume-collector\SKILL.md")
```

最后一行必须输出 `True`。如果不是 `True`，说明 zip 没有解到正确目录，不要继续 bootstrap。

首次使用先运行：

```powershell
powershell -ExecutionPolicy Bypass -File "$HOME\.codex\skills\boss-jd-resume-collector\scripts\bootstrap_boss_cli.ps1"
```

bootstrap 会执行单一路径安装：

- 下载用户目录内的 Node.js。
- 若系统没有 Git，则下载用户目录内的 MinGit。
- 从 `https://github.com/monster1389/boss-cli` clone `codex-resume-sync`。
- 执行 `npm ci` 和 `npm run build`。
- 生成 `~\.boss-cli\bin\boss.cmd` 和 `%APPDATA%\npm\boss.cmd`。
- 写入 `~\.boss-cli\toolchain\boss-command.json`，采集脚本会自动读取。

如需要指定分支或仓库：

```powershell
powershell -ExecutionPolicy Bypass -File "$HOME\.codex\skills\boss-jd-resume-collector\scripts\bootstrap_boss_cli.ps1" -RepoRef codex-resume-sync -RepoUrl https://github.com/monster1389/boss-cli
```

## 采集流程

1. 接收或保存用户提供的 JD 文本。
2. 从 JD 标题或正文识别岗位关键词；关键词不明确时先向用户确认。
3. 确认 BOSS 登录态：如未登录，先运行 `boss login` 并扫码。
4. 运行采集脚本。

PowerShell 入口会转调用同目录 Python 采集脚本：

```powershell
powershell -ExecutionPolicy Bypass -File "<skill_dir>\scripts\collect_boss_resumes.ps1" -JdFile "<jd.md>" -JobKeyword "<keyword>"
```

默认每个 `boss` 子命令最多等待 900 秒。需要调整时显式传入：

```powershell
powershell -ExecutionPolicy Bypass -File "<skill_dir>\scripts\collect_boss_resumes.ps1" -JdFile "<jd.md>" -JobKeyword "<keyword>" -CommandTimeoutSeconds 900
```

也可以直接使用 Python 入口：

```bash
python "<skill_dir>/scripts/collect_boss_resumes.py" --jd-file "<jd.md>" --job-keyword "<keyword>"
```

也可以直接传入 JD 文本：

```powershell
powershell -ExecutionPolicy Bypass -File "<skill_dir>\scripts\collect_boss_resumes.ps1" -JdText "<JD text>" -JobKeyword "<keyword>"
```

## 默认采集来源

默认采集并校验：

```text
boss resumes --from chat --limit 3 --json
boss resumes --from recommend --limit 3 --json --job <keyword>
boss resumes --from search --keyword <keyword> --limit 3 --json
```

采集前自检只检查：

```text
boss help
boss recommend <keyword>
boss resumes --from chat --limit 1 --json
```

如需收窄搜索页筛选，可以显式传入 search 专用参数：

```powershell
powershell -ExecutionPolicy Bypass -File "<skill_dir>\scripts\collect_boss_resumes.ps1" -JdFile "<jd.md>" -JobKeyword "<keyword>" -SearchKeyword "<keyword>" -SearchCity "<city>"
```

只有显式传入 `-SearchJob` / `--search-job` 时，search 命令才会追加 `--job`：

```text
boss resumes --from search --keyword <keyword> --job <job> --limit 3 --json
```

## 成功标准

- 默认模式下，`chat`、`recommend`、`search` 每个来源都必须有 3 份可用简历。
- 可用状态只包括 `downloaded` 和 `skipped_existing`。
- 每条可用结果都必须能找到本地 `resume.md` 和 `resume.json`。
- 任一请求来源不足 3 份时，本轮采集失败；但仍会报告失败来源、失败分类、候选人级错误，以及已经落地的可用简历总数。

## 输出

脚本会在下面目录生成一次 run：

```text
~/.boss-cli/runs/<timestamp>_<safe_jd_title>/
```

文件说明：

- `jd.md`：本轮采集使用的 JD。
- `collection_manifest.json`：机器可读的采集清单。
- `collection_summary.md`：给人看的中文摘要。

后续匹配逻辑只读取 `jd.md`、`collection_manifest.json`，以及 manifest 中列出的本地 `resume.md` / `resume.json` 文件。匹配阶段不要再实时访问 BOSS。

失败 run 也可能包含已成功落地的简历；后续匹配只能消费 manifest 中状态为 `downloaded` 或 `skipped_existing` 且本地文件存在的条目。

## 打包

交付单个 skill zip 时，只需要打包 `boss-jd-resume-collector` 目录本身，排除 `__pycache__` 和 `*.pyc`。解压到 Codex skills 目录后，按“零环境启动”运行 bootstrap。

## 安全约束

- 不给候选人发消息。
- 不修改职位状态。
- 默认触发 search 来源采集。
- 下载的简历属于敏感招聘数据，按私密数据处理。
