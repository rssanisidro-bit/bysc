# APK 构建状态

当前 Windows 环境未安装 `buildozer`、Java、Gradle、Android SDK/NDK，WSL/Ubuntu 也尚未完成可用初始化，因此本机暂时无法直接生成真实 `.apk` 安装包。

更简单的生成方式已经加入项目：使用 GitHub Actions 云端构建。把项目上传到 GitHub 后，进入 `Actions`，运行 `Build Android APK` 工作流，构建完成后从 `Artifacts` 下载 APK。

本项目已经提供：

- `main.py`：北洋闪传新版精美 UI + 完整文件速传功能。
- `main.py`：电脑端默认启动北洋闪传网页控制台，操作步骤与手机端一致，支持 TCP 直连发送/接收、保存目录设置和 SHA-256 校验。
- `main.py`：已优化传输超时、连接重试、断点续传提示、接收落盘和 Android 文件索引刷新，减少发送失败和文件管理器延迟显示问题。
- `main.py`：Android 发送端默认使用本机“文档安全选择页”，由浏览器选择 PDF、Word、压缩包等文档并上传回 App，绕开 Kivy/pyjnius 直接读取文档 Provider 时可能闪退的问题；对方 IP 会自动保存，重启后继续保留。
- `buildozer.spec`：Android APK 打包配置。
- `build_apk_linux.sh`：Linux / WSL 下的一键构建脚本。
- `.github/workflows/build-apk.yml`：GitHub Actions 云端 APK 构建流程。

在安装好 Linux/WSL + Java + Android SDK/NDK + Buildozer 后，进入项目目录执行：

```bash
chmod +x build_apk_linux.sh
./build_apk_linux.sh
```

成功后 APK 会生成在：

```text
bin/*.apk
```
