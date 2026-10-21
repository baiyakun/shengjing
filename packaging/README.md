# 打包发布

> 这些脚本还没有在 Mac 上实际跑过，属于第 0 阶段的验证内容。

## 需要准备

- Apple Silicon Mac，安装 uv、pnpm、Rust
- 苹果开发者账号（每年 99 美元），以及 “Developer ID Application” 证书
- 一个 **LGPL 构建**的 ffmpeg，放到 `packaging/vendor/ffmpeg`
  （不要用 GPL 构建，否则整个 App 都要遵守 GPL）

## 一键发布

```bash
export DEVELOPER_ID="Developer ID Application: 你的名字 (TEAMID)"
export APPLE_SIGNING_IDENTITY="$DEVELOPER_ID"
export APPLE_ID=你的苹果账号 APPLE_PASSWORD=App专用密码 APPLE_TEAM_ID=TEAMID
packaging/release.sh
```

步骤：

1. `build_runtime.sh`：复制一份独立的 Python 3.13（python-build-standalone），把主引擎、
   语音进程、mlx-speech、mlx-lm 直接装进去（不用 venv，因为 venv 写死了绝对路径，不能搬移）。
2. `sign_runtime.sh`：给里面每个动态库和可执行文件签名（加固运行时 + 时间戳），
   Python 使用 `entitlements/python.plist` 里的权限。
3. `pnpm tauri build --config src-tauri/tauri.release.conf.json`：把运行环境打进
   `Shengjing.app/Contents/Resources/runtime/`，签名 App，生成 dmg 并公证。

## 扩展包

```bash
packaging/build_extension.sh analysis
ACESTEP_REF=<验证过的提交> packaging/build_extension.sh music
```

输出 `packaging/build/extensions/<名称>-<版本>.tar.gz` 和 sha256。上传到你自己的下载服务器后，
App 通过 `extension.install` 任务下载、校验并解压到数据目录的 `extensions/` 下。
从网络安装时必须提供 sha256，解压时拒绝绝对路径和指向目录外的链接。

## 为什么不上 App Store

App Store 要求沙盒。沙盒里运行内置的 Python 解释器、启动子进程、下载并加载扩展包里的动态库
都有很多限制，所以先用官网 dmg 分发。
