# 小说项目技能链蛋白树

## 层级结构

```
蛋白质层 (Protein Level) - 3节点链 + STOPJRuntime闸门调控
├── NAV: AI小说编辑器-高级版
│   ├── NA (多肽): AI小说生成器-新建
│   ├── AIEDITOR (多肽): AI小说编辑器-分析/编辑/导出
│   └── VIP (多肽): VIP计费系统-高级功能解锁
│
└── 闸门: STOPJRuntime
```

## 蛋白定义

### NAV - AI小说编辑器-高级版
- **基因链**: N-A-V
- **功能**: 结合生成器与编辑器的完整创作工作流
- **阶段**: 新建 → 编辑 → 计费
- **特征**: 小说创作、AI编辑、VIP计费、生图、文档导出

## 多肽层 (Polypeptide Level) - 2原子组合

### NA - AI小说生成器-新建
- **基因链**: N-A
- **功能**: 创建新小说项目

### NC - AI小说生成器-文档导出
- **基因链**: N-C
- **功能**: 导出Word/PDF文档

### NG - AI小说生成器-角色生图
- **基因链**: N-G
- **功能**: 角色形象生成

### NE - AI小说生成器-场景生成
- **基因链**: N-E
- **功能**: 场景插图生成

## 原子层 (Atom Level)

### A系列 (腺嘌呤)
- **A1**: MIG记忆生图 - 从HERB知识库智能读取提示词生图
- **A5**: 记忆同步 - 同步记忆到HERB知识库

### C系列 (胞嘧啶)
- **C3**: Agnes生图 - 使用Agnes模型生成图像
- **C8**: 人物工作流生图 - 为同一人物生成多个视角和动作的图像工作流
- **C9**: PDF中文乱码解决 - 解决PDF中文乱码问题

### G系列 (鸟嘌呤)
- **G4**: 文件发送 - 发送文件给用户

### T系列 (胸腺嘧啶)
- **T2**: 视觉识别 - 使用Agnes AI视觉模型识别图片
- **T6**: 技能规划 - 规划技能调用链

## 依赖关系

```
novel-generator 依赖:
├── memory-image-generation (A1)
├── agnes-image (C3)
├── docx (外部)
└── markdown-converter (外部)

novel-editor 依赖:
├── docx (外部)
└── markdown-converter (外部)

NAV蛋白依赖:
├── NA多肽 → novel-generator
├── AIEDITOR多肽 → novel-editor
└── VIP多肽 → vip_billing原子
```

## 快捷命令

```bash
/novel create <主题> --style <风格>      # 创建新小说
/novel generate <章节> --words <数量>    # 生成章节
/novel character <角色> --image          # 生成角色图
/novel scene <场景> --image              # 生成场景图
/novel export --format docx|md|txt       # 导出文档
/novel analyze <文件>                    # 分析已有小说
```

## 版本信息
- **创建时间**: 2026-08-25
- **系统**: AGES技能链
- **层级**: 原子 → 多肽 → 蛋白
