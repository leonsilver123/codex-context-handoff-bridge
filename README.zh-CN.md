# Codex Context Handoff Bridge

[English](README.md) | 中文

`Codex Context Handoff Bridge` 是一个面向 Codex App 长任务的项目本地上下文压缩与线程交接工具。

它把任务状态、架构决策、待办事项、证据日志和下一线程提示保存到 `.codex-handoff/` 目录中，而不是只依赖单个聊天窗口的历史记录。当当前 Codex 线程变长时，你可以生成结构化交接文件，创建新的同工作区 Codex 线程，让新线程读取这些文件并继续工作。

## 核心能力

- 项目本地 `.codex-handoff/` 状态目录。
- 结构化 `handoff.md`，包含 `FACT`、`DECISION`、`TODO`、`OPEN`、`REJECTED`、`EVIDENCE`。
- 自动生成 `next_prompt.md`。
- 证据日志压缩。
- 上下文压力估算。
- 目标线程 `accept-handoff` 验收。
- 完成度审计 `completion-audit`。
- 可选 `codex://` Deep Link 探针。
- Windows PowerShell 安装器和包装器。
- GitHub Actions CI。
- CLI 普通输出支持 `--lang zh-CN`。

## 环境要求

- Windows PowerShell。
- Python 3。
- Codex App，真实线程交接验收时需要。

Python 实现仅使用标准库。

## 安装到你的项目

下载或克隆仓库：

```powershell
git clone https://github.com/leonsilver123/codex-context-handoff-bridge.git
cd codex-context-handoff-bridge
```

安装到目标项目：

```powershell
.\install.ps1 -ProjectPath "C:\path\to\your-project"
```

进入目标项目并检查：

```powershell
cd "C:\path\to\your-project"
.\scripts\codex-handoff.ps1 doctor --json
```

## 基本使用

初始化或修复脚手架：

```powershell
.\scripts\codex-handoff.ps1 init
.\scripts\codex-handoff.ps1 verify
```

生成或刷新交接文件：

```powershell
.\scripts\codex-handoff.ps1 auto --link
```

中文普通输出：

```powershell
.\scripts\codex-handoff.ps1 doctor --lang zh-CN
.\scripts\codex-handoff.ps1 status --lang zh-CN
.\scripts\codex-handoff.ps1 completion-audit --lang zh-CN
```

机器可读 JSON 输出仍保持英文键名，方便自动化：

```powershell
.\scripts\codex-handoff.ps1 doctor --json
```

## 推荐交接流程

1. 在旧线程所在项目中运行：

```powershell
.\scripts\codex-handoff.ps1 auto --link
```

2. 创建一个新的同工作区 Codex 线程。

3. 让新线程读取：

```text
AGENTS.md
.codex-handoff/current_state.yaml
.codex-handoff/handoff.md
.codex-handoff/decisions.yaml
.codex-handoff/evidence_summary.md
.codex-handoff/evidence.jsonl
.codex-handoff/next_prompt.md
```

4. 在新线程运行：

```powershell
.\scripts\codex-handoff.ps1 accept-handoff --thread-id <new-thread-id> --json
```

5. 回到项目中审计：

```powershell
.\scripts\codex-handoff.ps1 completion-audit --json --write
```

## 中文文档

- [安装说明](docs/zh-CN/INSTALL.md)
- [使用说明](docs/zh-CN/USAGE.md)

