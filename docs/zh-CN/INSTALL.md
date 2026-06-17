# 安装说明

`Codex Context Handoff Bridge` 是一个轻量级项目本地工具。它只需要 Windows PowerShell 和 Python 3。

## 从 GitHub 安装

```powershell
git clone https://github.com/leonsilver123/codex-context-handoff-bridge.git
cd codex-context-handoff-bridge
.\install.ps1 -ProjectPath "C:\path\to\your-project"
```

安装脚本会复制 `scripts/` 到你的目标项目，创建 `.codex-handoff/` 脚手架，并运行 `verify`。

如果目标项目已有同名脚本，并且你确认要覆盖：

```powershell
.\install.ps1 -ProjectPath "C:\path\to\your-project" -Force
```

## Python 路径

包装器会按顺序查找：

1. `CODEX_HANDOFF_PYTHON`
2. `py -3`
3. `python`

如果你的 Python 在自定义位置：

```powershell
$env:CODEX_HANDOFF_PYTHON = "C:\Python312\python.exe"
```

## 验证安装

进入目标项目：

```powershell
cd "C:\path\to\your-project"
.\scripts\codex-handoff.ps1 verify
.\scripts\codex-handoff.ps1 doctor --lang zh-CN
```

如果需要完整 smoke 检查：

```powershell
.\scripts\handoff-smoke.ps1 -SkipAuto
```
