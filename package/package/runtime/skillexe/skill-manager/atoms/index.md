# skill-manager 原子库

## 原子定义

| 原子ID | 名称 | 描述 | 所属技能 |
|--------|------|------|----------|
| SM-001 | backup | 备份技能功能 | skill-manager |
| SM-002 | install | 安装技能功能 | skill-manager |
| SM-003 | create | 创建技能功能 | skill-manager |
| SM-004 | delete | 删除技能功能 | skill-manager |
| SM-005 | encrypt | 加密配置功能 | skill-manager |
| SM-006 | decrypt | 解密配置功能 | skill-manager |
| SM-007 | list | 列表查询功能 | skill-manager |
| SM-008 | status | 状态检查功能 | skill-manager |

## 原子脚本

每个原子对应一个脚本文件，位于scripts/目录：

```
scripts/
├── backup.sh      # 备份脚本
├── install.sh     # 安装脚本
├── create.sh      # 创建脚本
├── delete.sh      # 删除脚本
├── encrypt.sh     # 加密脚本
├── decrypt.sh     # 解密脚本
├── list.sh        # 列表脚本
└── status.sh      # 状态脚本
```
