# 打包发布

> 这些脚本还没有在 Mac 上实际跑过，属于第 0 阶段的验证内容。

## 需要准备

- Apple 芯片的 Mac，安装 uv、pnpm、Rust（Intel 版本也可以在 Intel Mac 上构建）
- 苹果开发者账号（每年 99 美元），以及 “Developer ID Application” 证书
- 一个 **LGPL 构建**的 ffmpeg，放到 `packaging/vendor/ffmpeg-arm64` / `packaging/vendor/ffmpeg-x86_64`，
  或者一个同时包含两种架构的通用版本放到 `packaging/vendor/ffmpeg`
  （不要用 GPL 构建，否则整个 App 都要遵守 GPL）

## 一键发布

```bash
export DEVELOPER_ID="Developer ID Application: 你的名字 (TEAMID)"
export APPLE_SIGNING_IDENTITY="$DEVELOPER_ID"
export APPLE_ID=你的苹果账号 APPLE_PASSWORD=App专用密码 APPLE_TEAM_ID=TEAMID
packaging/release.sh                 # Apple 芯片版本
ARCH=x86_64 packaging/release.sh     # Intel 版本（只能使用在线 API）
```

两个版本分别生成 `Shengjing_<版本>_aarch64.dmg` 和 `Shengjing_<版本>_x64.dmg`。
Intel 版本只带主引擎（没有 mlx-speech、mlx-lm），体积小很多，App 会自动进入在线模式。
它可以在 Intel Mac 上构建，也可以在装了 Rosetta 的 Apple 芯片 Mac 上构建。
不做“通用版本”：两种架构要带的 Python 运行环境完全不同，合在一起只会让下载变大。

步骤：

1. `build_runtime.sh`：复制一份对应架构的独立 Python 3.13（python-build-standalone），把主引擎、
   语音进程、mlx-speech、mlx-lm（Intel 版本只有主引擎）直接装进去（不用 venv，因为 venv 写死了绝对路径，不能搬移）。
2. `sign_runtime.sh`：给里面每个动态库和可执行文件签名（加固运行时 + 时间戳），
   Python 使用 `entitlements/python.plist` 里的权限。
3. `pnpm tauri build --config src-tauri/tauri.release.conf.json`：把运行环境打进
   `Shengjing.app/Contents/Resources/runtime/`，签名 App，生成 dmg 并公证。

## 扩展包

扩展包只有 Apple 芯片版本（Intel 芯片的 Mac 不能运行本地模型）。

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
