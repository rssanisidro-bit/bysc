# 北洋闪传

`北洋闪传` 是根据 `lec22 网络编程.pdf` 中“挑战 2：文件速传”实现的文件传输程序。Android 端提供 APK 原生界面，电脑端运行 `python main.py` 后提供网页控制台，二者都使用同一套 TCP 文件传输协议。

## 核心功能

- 发送方选择本地文件，接收方选择保存位置。
- 支持发送前压缩 ZIP、接收后自动解压 ZIP。
- 支持进度、速度、剩余时间显示。
- 支持取消传输、断点续传和 SHA-256 完整性校验。
- 支持输入 `IP` 或 `IP:端口`，默认 TCP 端口为 `50022`。
- 对方 IP 会自动保存，应用重启后仍保留上次输入。
- Android 接收完成后会主动刷新系统文件索引，减少文件管理器延迟显示。
- Android 文档选择不再使用浏览器网页。PDF、Word、压缩包等文档由系统文件选择器返回给 `NativeFileBridge.java`，再由 Android 原生层复制到 App 缓存目录，避免 Kivy 直接读取 `content://` 时闪退。

## Android 保存位置

推荐接收方保存到：

- `/storage/emulated/0/Download/北洋闪传`
- `/storage/emulated/0/Documents/北洋闪传`
- `/storage/emulated/0/DCIM/北洋闪传`

不建议优先使用 `/storage/emulated/0/Android/data/...`，因为部分 Android 11+ 手机文件管理器会隐藏这个目录。

## 使用流程

1. 两台设备连接同一局域网、同一热点，或没有客户端隔离的校园网。
2. 接收方打开 App，查看首页显示的“我的 IP”。
3. 接收方选择保存位置，然后点击“开始接收”。
4. 发送方输入接收方 IP。
5. 发送方点击“选择文件”，选择图片、视频、PDF、Word、压缩包等文件。
6. 发送方点击“立即发送”。
7. 传输完成后，接收方到保存目录查看文件。

## 电脑端运行

安装依赖：

```bash
pip install -r requirements.txt
```

启动电脑端网页控制台：

```bash
python main.py
```

电脑端默认网页端口是 `50023`，文件传输 TCP 端口仍是 `50022`。如果想在电脑上打开旧的 Kivy 窗口，可以运行：

```bash
python main.py --kivy
```

## APK 构建

最简单方法是使用 GitHub Actions：

1. 将本目录解压后的全部内容上传到仓库根目录。
2. 必须保留这些关键文件和目录：`main.py`、`buildozer.spec`、`.github/workflows/build-apk.yml`、`android_src/`、`assets/`。
3. 打开仓库的 `Actions` 页面。
4. 运行 `Build Android APK`。
5. 构建成功后在 `Artifacts` 下载 `BeiyangFlash-debug-apk`，解压即可得到 APK。

首次云端构建可能需要 20 到 60 分钟，因为要下载 Android SDK/NDK 和编译依赖。

## 校园网说明

本程序默认是 TCP 局域网直连。如果热点或普通路由器可以传，但校园 Wi-Fi 不可以，通常是校园网启用了客户端隔离或入站 TCP 限制。此时可以：

- 改用手机热点或不隔离的路由器。
- 关闭移动数据，只保留 Wi-Fi 后刷新 IP。
- 确认发送方输入的是接收方 App 显示的 Wi-Fi IP。
- 如果校园网强制隔离，直连程序无法绕过，需要增加中继服务器模式。

## PDF 要求对应

| PDF 挑战要求 | 本项目实现 |
| --- | --- |
| 发送方选择文件 | Android 原生选择器、电脑网页选择文件 |
| 接收方选择保存位置 | Android 保存位置面板、电脑网页保存目录输入 |
| 文件压缩 / 解压缩 | ZIP 压缩和自动解压 |
| 传输进度 | 百分比、速度、剩余时间 |
| 取消传输 | 双端都可取消 |
| 断点续传 | `.part` + `.state.json` + offset 协商 |
| 文件校验 | SHA-256 |
| 手机和桌面设备 | Android APK + 电脑网页模式 |
