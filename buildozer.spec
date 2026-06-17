[app]
title = 北洋闪传
package.name = beiyangflashtransfer
package.domain = org.tju.challenge
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,md,txt,java
source.exclude_dirs = __pycache__,.buildozer,bin
version = 1.0
requirements = python3,kivy,plyer
icon.filename = assets/icon.png
orientation = portrait
fullscreen = 0

# Android needs network permission for socket communication.
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,READ_MEDIA_IMAGES,READ_MEDIA_VIDEO,READ_MEDIA_AUDIO,MANAGE_EXTERNAL_STORAGE
android.api = 35
android.minapi = 24
android.ndk_api = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
android.add_src = android_src

[buildozer]
log_level = 2
warn_on_root = 1
