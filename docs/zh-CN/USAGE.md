# 使用说明

## 初始化

```powershell
.\scripts\codex-handoff.ps1 init
.\scripts\codex-handoff.ps1 verify
```

## 查看状态

中文普通输出：

```powershell
.\scripts\codex-handoff.ps1 status --lang zh-CN
.\scripts\codex-handoff.ps1 doctor --lang zh-CN
```

机器可读输出：

```powershell
.\scripts\codex-handoff.ps1 status --json
.\scripts\codex-handoff.ps1 doctor --json
```

## 生成交接

```powershell
.\scripts\codex-handoff.ps1 auto --link
```

该命令会刷新：

```text
.codex-handoff/current_state.yaml
.codex-handoff/handoff.md
.codex-handoff/next_prompt.md
.codex-handoff/evidence.jsonl
.codex-handoff/thread_registry.json
```

## 新线程验收

在新的 Codex 线程中读取 `next_prompt.md` 列出的文件，然后运行：

```powershell
.\scripts\codex-handoff.ps1 accept-handoff --thread-id <new-thread-id> --json
```

## 完成度审计

```powershell
.\scripts\codex-handoff.ps1 completion-audit --lang zh-CN
.\scripts\codex-handoff.ps1 completion-audit --json --write
```

## 证据压缩

查看上下文压力：

```powershell
.\scripts\codex-handoff.ps1 context-status --json
```

压缩证据日志：

```powershell
.\scripts\codex-handoff.ps1 compact-evidence --json
```

## Deep Link

Deep Link 是可选探针，不作为唯一验收依据：

```powershell
.\scripts\codex-handoff.ps1 open-link --json --write
.\scripts\codex-handoff.ps1 open-link --execute --json --write
```
