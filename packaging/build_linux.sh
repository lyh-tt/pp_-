#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# 在 WSL (/mnt/*) 下使用原生 ext4 临时环境构建，避免 NTFS 权限与速度瓶颈
case "$ROOT" in
    /mnt/*) VENV_DIR="${UV_PROJECT_ENVIRONMENT:-/tmp/ms-linux-venv}" ;;
    *)      VENV_DIR="${UV_PROJECT_ENVIRONMENT:-$ROOT/.venv}" ;;
esac
export UV_PROJECT_ENVIRONMENT="$VENV_DIR"

WORK_DIR="/tmp/pyi-multiscale-build"
DIST_TMP="/tmp/pyi-multiscale-dist"
STAGE_DIR="/tmp/pyi-multiscale-stage"
PKG_NAME="multiscale-toolbox-linux-x86_64"

echo "=== 1. Sync Linux uv environment ($VENV_DIR) ==="
cd "$ROOT"
uv sync --locked

echo "=== 2. Run Linux pytest suite ==="
QT_QPA_PLATFORM=offscreen "$VENV_DIR/bin/python" -m pytest -v tests

echo "=== 3. Build standalone Linux x86_64 binary ==="
rm -rf "$WORK_DIR" "$DIST_TMP" "$STAGE_DIR"
mkdir -p "$WORK_DIR" "$DIST_TMP" "$STAGE_DIR/$PKG_NAME"
"$VENV_DIR/bin/pyinstaller" --noconfirm --clean \
    --workpath "$WORK_DIR" \
    --distpath "$DIST_TMP" \
    packaging/toolbox.spec

echo "=== 4. Verify frozen Linux binary (--self-check) ==="
CHECK_JSON="/tmp/ms_linux_check.json"
rm -f "$CHECK_JSON"
QT_QPA_PLATFORM=offscreen "$DIST_TMP/multiscale-toolbox" --self-check "$CHECK_JSON"
cat "$CHECK_JSON"
rm -f "$CHECK_JSON"

GLIBC_VER="$(ldd --version 2>&1 | head -n 1 | awk '{print $NF}')"

echo "=== 5. Assemble Linux release archive ==="
BUNDLE_DIR="$STAGE_DIR/$PKG_NAME"
cp "$DIST_TMP/multiscale-toolbox" "$BUNDLE_DIR/multiscale-toolbox"
cp "$ROOT/src/multiscale/ui/assets/app_icon.png" "$BUNDLE_DIR/app_icon.png"
chmod 0755 "$BUNDLE_DIR/multiscale-toolbox"
chmod 0644 "$BUNDLE_DIR/app_icon.png"

cat > "$BUNDLE_DIR/install_desktop.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ICON_DIR="$HOME/.local/share/icons/hicolor/512x512/apps"
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$ICON_DIR" "$APP_DIR"
cp -f "$HERE/app_icon.png" "$ICON_DIR/multiscale-toolbox.png"
cat > "$APP_DIR/multiscale-toolbox.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=材料多尺度建模处理工具箱
GenericName=Multiscale Modeling Toolbox
Comment=Composite multi-scale FEA modeling and Abaqus batch runner
Exec="$HERE/multiscale-toolbox" %F
Icon=multiscale-toolbox
Terminal=false
Categories=Science;Engineering;
StartupWMClass=multiscale-toolbox
DESKTOP
chmod 0644 "$APP_DIR/multiscale-toolbox.desktop"
echo "Installed desktop entry -> $APP_DIR/multiscale-toolbox.desktop"
EOF
chmod 0755 "$BUNDLE_DIR/install_desktop.sh"

cat > "$BUNDLE_DIR/README_LINUX.txt" <<EOF
材料多尺度建模处理工具箱 (Linux x86_64)
========================================

1. 运行方式：
   chmod +x ./multiscale-toolbox ./install_desktop.sh
   ./multiscale-toolbox

2. 安装桌面应用图标与菜单快捷方式（可选）：
   ./install_desktop.sh

3. 无图形界面服务器仅运行命令行独立求解脚本：
   在 Windows 或桌面界面点击生成/导出后，将 run_fiber_parallel.py 或
   run_abaqus_batch.py 连同对应数据目录上传到服务器即可用 python3 直接运行。

4. 系统依赖说明：
   - 构建环境 glibc 版本：$GLIBC_VER
   - 桌面模式需要系统安装常见 X11/Wayland 与 OpenGL 基础库（例如 libGL.so.1 / libEGL.so.1 / libxcb-cursor0 等）。
   - 如果目标服务器 glibc 版本低于 $GLIBC_VER，请在该目标 Linux 机器拉取源码后直接执行：
     bash packaging/build_linux.sh
EOF
chmod 0644 "$BUNDLE_DIR/README_LINUX.txt"

mkdir -p "$ROOT/dist/linux"
cp -f "$BUNDLE_DIR/multiscale-toolbox" "$ROOT/dist/linux/multiscale-toolbox"
cp -f "$BUNDLE_DIR/app_icon.png" "$ROOT/dist/linux/app_icon.png"
cp -f "$BUNDLE_DIR/install_desktop.sh" "$ROOT/dist/linux/install_desktop.sh"
cp -f "$BUNDLE_DIR/README_LINUX.txt" "$ROOT/dist/linux/README_LINUX.txt"

TAR_PATH="$ROOT/dist/$PKG_NAME.tar.gz"
tar -C "$STAGE_DIR" -czf "$TAR_PATH" "$PKG_NAME"
rm -rf "$WORK_DIR" "$DIST_TMP" "$STAGE_DIR"

echo "Linux build complete:"
echo "  - $TAR_PATH"
echo "  - $ROOT/dist/linux/multiscale-toolbox"
