# MathModelAgent for DeepSeek Harness

本目录是 MathModelAgent 在 DeepSeek Harness（DSH）上的发布物。

## 1. Agent 预设（当前可用，已验证）

`preset-mathmodel/` 是一个完整可用的 DSH agent preset：standard 全能力 + 数学建模 persona（主入口 `$1start-mathmodel`，含完整性纪律与人机门禁原则）。已在本机 mount 校验通过（`standingKeyFor('mathmodel')` 正常返回）。

安装（把两个文件放入用户预设根）：

```powershell
mkdir "$env:USERPROFILE\.dsh\.agent-presets\mathmodel" -Force
Copy-Item dsh\preset-mathmodel\* "$env:USERPROFILE\.dsh\.agent-presets\mathmodel" -Force
```

然后新建会话时选择"数学建模"预设；技能发现依赖工作区内的 `.agents/skills`（先运行 `scripts/setup-codex.ps1` 建立联结）。

## 2. Profile Bundle（官方外部插件分发路径，下一步）

按 DSH 当前版本（2026-08-09 起）的官方口径，独立的第三方插件分发路径是 **installable profile bundle**：

```powershell
dsh plugin --profile <profile> add <package-or-git-spec>
```

被安装的包通过 `dsh.bundle.patch` 贡献 `cordis.patch.yml` 补丁层，贡献 Skills 时挂载 `@deepseek-ai/dsh-skill-filesystem`。注意：**"包内随附 skill 目录的声明式路径"在该版本是官方列名的覆盖缺口**（见 harness checkout 的 `2026-08-09-remove-repository-plugin.md`），因此当前推荐组合是：bundle/preset 提供 persona 与流程胶水，skills 由本仓库的 `skills/` 目录经工作区发现供给。旧的 `.dsh-plugin` 仓库插件机制已被移除，不要再按它开发。

待官方覆盖缺口闭合后，本目录将补充 `bundle/`（package.json + dsh.bundle.patch + cordis.patch.yml），把 skill 目录随包分发。

## 3. 已知环境要求（DSH 沙箱）

- MATLAB 与 xelatex/MiKTeX 在默认沙箱下无法启动（需更宽执行权限 + TMP 重定向），子代理会话通常无法扩权，需要主控代跑——详见 `skills/doctor/SKILL.md` 的"沙箱化 Harness 环境"节；
- 本机无 bash 时 `writing_check.sh` 用等价 Python 执行，`bash -n` 由 CI 承担。
