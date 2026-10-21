#!/bin/bash
# C2技能安装脚本 - STOPJ Runtime闸门技能安装器
# 用法: ./install.sh [install|backup|restore] [source]

set -e

SKILL_NAME="C2"
SKILL_DIR="/tmp/storage/sda1/.oray_app/cow/skills/${SKILL_NAME}"
BACKUP_DIR="/tmp/storage/sda1/.oray_app/cow/backups/c2_skill"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ARCHIVE_NAME="c2_skill_${TIMESTAMP}.tar.gz"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 安装技能
install_skill() {
    local source="${1:-$BACKUP_DIR/latest}"
    
    log_info "📦 安装C2技能..."
    log_info "来源: $source"
    
    if [[ ! -d "$source" ]]; then
        log_error "源目录不存在: $source"
        exit 1
    fi
    
    # 创建目标目录
    mkdir -p "$SKILL_DIR"/{atoms,polypeptides,scripts}
    
    # 复制原子文件
    if [[ -d "$source/atoms" ]]; then
        cp -r "$source/atoms/"* "$SKILL_DIR/atoms/" 2>/dev/null || true
        log_info "✅ 已安装原子文件"
    fi
    
    # 复制多肽文件
    if [[ -d "$source/polypeptides" ]]; then
        cp -r "$source/polypeptides/"* "$SKILL_DIR/polypeptides/" 2>/dev/null || true
        log_info "✅ 已安装多肽文件"
    fi
    
    # 复制主文件
    if [[ -f "$source/STOPJ.json" ]]; then
        cp "$source/STOPJ.json" "$SKILL_DIR/"
        log_info "✅ 已安装STOPJ蛋白"
    fi
    
    # 复制SKILL.md
    if [[ -f "$source/SKILL.md" ]]; then
        cp "$source/SKILL.md" "$SKILL_DIR/"
        log_info "✅ 已安装SKILL.md"
    fi
    
    # 创建软链接到技能库
    ln -sf "$SKILL_DIR" /tmp/storage/sda1/.oray_app/cow/skills/C2
    
    log_info "🎉 C2技能安装完成!"
    log_info "快捷命令: /C2"
}

# 备份多肽
backup_peptides() {
    local target="${1:-latest}"
    local backup_path="$BACKUP_DIR/$target"
    
    log_info "💾 备份C2技能多肽..."
    
    # 创建备份目录
    mkdir -p "$backup_path"/{atoms,polypeptides}
    
    # 备份原子
    if [[ -d "$SKILL_DIR/atoms" ]]; then
        cp "$SKILL_DIR/atoms/"*.json "$backup_path/atoms/" 2>/dev/null || true
        log_info "✅ 已备份原子文件"
    fi
    
    # 备份多肽
    if [[ -d "$SKILL_DIR/polypeptides" ]]; then
        cp "$SKILL_DIR/polypeptides/"*.json "$backup_path/polypeptides/" 2>/dev/null || true
        log_info "✅ 已备份多肽文件"
    fi
    
    # 备份主文件
    cp "$SKILL_DIR/STOPJ.json" "$backup_path/" 2>/dev/null || true
    cp "$SKILL_DIR/SKILL.md" "$backup_path/" 2>/dev/null || true
    
    # 创建tar包
    cd "$backup_path/.."
    tar -czf "$ARCHIVE_NAME" "$target/" 2>/dev/null || true
    
    # 更新latest软链接
    ln -sf "$target" "$BACKUP_DIR/latest"
    
    log_info "📦 备份完成: $ARCHIVE_NAME"
    log_info "📁 备份路径: $backup_path"
}

# 从备份恢复
restore_skill() {
    local archive="${1:-$BACKUP_DIR/$ARCHIVE_NAME}"
    
    log_info "🔧 从备份恢复C2技能..."
    
    if [[ ! -f "$archive" ]]; then
        log_error "备份文件不存在: $archive"
        exit 1
    fi
    
    # 创建临时目录
    local temp_dir=$(mktemp -d)
    
    # 解压
    tar -xzf "$archive" -C "$temp_dir"
    
    # 安装
    install_skill "$temp_dir"
    
    # 清理
    rm -rf "$temp_dir"
    
    log_info "🎉 恢复完成!"
}

# 显示帮助
show_help() {
    echo "C2技能安装器 - STOPJ Runtime闸门技能"
    echo ""
    echo "用法:"
    echo "  $0 install [source]    # 从指定目录安装技能"
    echo "  $0 backup [target]     # 备份多肽到指定目录"
    echo "  $0 restore [archive]   # 从备份文件恢复"
    echo "  $0 status              # 查看技能状态"
    echo "  $0 help                # 显示此帮助"
    echo ""
    echo "示例:"
    echo "  $0 backup v1.0        # 备份多肽到v1.0目录"
    echo "  $0 install ./backup   # 从backup目录安装"
    echo "  $0 restore c2_skill_20260823.tar.gz  # 恢复备份"
}

# 显示状态
show_status() {
    echo "=== C2技能状态 ==="
    echo ""
    
    # 检查目录
    if [[ -d "$SKILL_DIR" ]]; then
        log_info "✅ 技能目录存在: $SKILL_DIR"
    else
        log_error "❌ 技能目录不存在: $SKILL_DIR"
    fi
    
    # 统计文件
    local atom_count=$(find "$SKILL_DIR/atoms" -name "*.json" 2>/dev/null | wc -l)
    local peptide_count=$(find "$SKILL_DIR/polypeptides" -name "*.json" 2>/dev/null | wc -l)
    local protein_count=$(find "$SKILL_DIR" -maxdepth 1 -name "*.json" 2>/dev/null | wc -l)
    
    echo "   原子文件: $atom_count"
    echo "   多肽文件: $peptide_count"
    echo "   蛋白文件: $protein_count"
    
    # 检查备份
    if [[ -d "$BACKUP_DIR" ]]; then
        local backup_count=$(ls -1 "$BACKUP_DIR"/*.tar.gz 2>/dev/null | wc -l)
        echo "   备份文件: $backup_count"
        if [[ -L "$BACKUP_DIR/latest" ]]; then
            echo "   最新备份: $(readlink "$BACKUP_DIR/latest")"
        fi
    else
        echo "   备份文件: 0"
    fi
    echo ""
}

# 主逻辑
case "${1}" in
    install)
        install_skill "$2"
        ;;
    backup)
        backup_peptides "$2"
        ;;
    restore)
        restore_skill "$2"
        ;;
    status)
        show_status
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        show_help
        exit 1
        ;;
esac
