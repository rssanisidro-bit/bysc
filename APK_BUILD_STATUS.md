# APK 构建状态

当前 Windows 环境没有完整的 Buildozer、Android SDK/NDK 构建链，因此本机不直接生成 APK。项目已提供 GitHub Actions 云端构建方案。

已包含的关键文件：

- `main.py`：北洋闪传 Android 界面 + 电脑端网页控制台 + TCP 文件传输逻辑。
- `android_src/org/tju/challenge/beiyangflashtransfer/NativeFileBridge.java`：Android 原生文档读取桥接，PDF、Word、压缩包等文件不再经过浏览器网页选择。
- `buildozer.spec`：APK 打包配置，已经接入 `android_src`。
- `.github/workflows/build-apk.yml`：GitHub Actions 云端 APK 构建流程。
- `assets/icon.png`：蓝色系应用图标。

上传到 GitHub 时，请把本目录解压后的内容直接放在仓库根目录，尤其不要漏掉隐藏目录 `.github` 和 Java 源码目录 `android_src`。

构建成功后，APK 会在 Actions 运行结果的 `Artifacts` 中显示为 `BeiyangFlash-debug-apk`。
