import hashlib
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("KIVY_NO_ARGS", "1")

from kivy.utils import platform
from kivy.app import App
from kivy.clock import mainthread
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.filechooser import FileChooserIconView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput


PORT = 50022
WEB_CONTROL_PORT = 50023
CHUNK_SIZE = 64 * 1024
SOCKET_TIMEOUT = 20.0
TRANSFER_TIMEOUT = 180.0
VERIFY_TIMEOUT = 300.0
WEB_CHUNK_SIZE = 1024 * 1024
APP_SETTINGS_FILENAME = "beiyang_flash_settings.json"
SEND_CONNECT_ATTEMPTS = 3
ANDROID_FILE_PICK_REQUEST = 50024
ANDROID_SAFE_UPLOAD_PORT = 50025

COLORS = {
    "bg": (0.95, 0.98, 1.0, 1),
    "surface": (1, 1, 1, 1),
    "surface_soft": (0.96, 0.98, 1.0, 1),
    "primary": (0.05, 0.45, 0.88, 1),
    "primary_dark": (0.04, 0.19, 0.37, 1),
    "success": (0.03, 0.52, 0.34, 1),
    "warning": (0.84, 0.39, 0.04, 1),
    "danger": (0.82, 0.14, 0.22, 1),
    "text": (0.05, 0.09, 0.15, 1),
    "muted": (0.39, 0.46, 0.55, 1),
    "border": (0.84, 0.89, 0.95, 1),
    "cyan_soft": (0.88, 0.96, 1.0, 1),
    "green_soft": (0.90, 0.98, 0.94, 1),
    "red_soft": (1.0, 0.93, 0.94, 1),
}

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/system/fonts/DroidSansFallback.ttf",
]

APP_FONT = "Roboto"
for font_path in FONT_CANDIDATES:
    if os.path.exists(font_path):
        LabelBase.register(name="FileLinkCJK", fn_regular=font_path)
        APP_FONT = "FileLinkCJK"
        break


def request_android_storage_permissions():
    if platform != "android":
        return
    try:
        from android.permissions import Permission, request_permissions

        names = [
            "READ_EXTERNAL_STORAGE",
            "WRITE_EXTERNAL_STORAGE",
            "READ_MEDIA_IMAGES",
            "READ_MEDIA_VIDEO",
            "READ_MEDIA_AUDIO",
        ]
        permissions = [getattr(Permission, name) for name in names if hasattr(Permission, name)]
        request_permissions(permissions)
    except Exception:
        pass


def first_existing_path(candidates):
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return str(Path.home())


def writable_directory(candidate):
    if not candidate:
        return False
    try:
        os.makedirs(candidate, exist_ok=True)
        probe = os.path.join(candidate, ".beiyang_flash_write_test")
        with open(probe, "wb") as f:
            f.write(b"ok")
        try:
            os.remove(probe)
        except OSError:
            pass
        return True
    except Exception:
        return False


def default_file_path():
    if platform == "android":
        return first_existing_path(
            [
                "/storage/emulated/0/Download",
                "/storage/emulated/0/Downloads",
                "/storage/emulated/0/DCIM",
                "/storage/emulated/0/Documents",
                "/storage/emulated/0",
            ]
        )
    return str(Path.home())


def default_save_path():
    if platform == "android":
        return "/storage/emulated/0/Download/北洋闪传"
    return str(Path.home() / "Downloads")


def android_app_external_files_path():
    if platform != "android":
        return None
    try:
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        folder = activity.getExternalFilesDir(None)
        if folder:
            return os.path.join(folder.getAbsolutePath(), "北洋闪传")
    except Exception:
        pass
    return "/storage/emulated/0/Android/data/org.tju.challenge.beiyangflashtransfer/files/北洋闪传"


def android_save_choices():
    choices = [
        ("下载/北洋闪传（推荐）", "/storage/emulated/0/Download/北洋闪传"),
        ("文档/北洋闪传", "/storage/emulated/0/Documents/北洋闪传"),
        ("相册/北洋闪传", "/storage/emulated/0/DCIM/北洋闪传"),
        ("下载根目录", "/storage/emulated/0/Download"),
    ]
    app_path = android_app_external_files_path()
    if app_path:
        choices.append(("应用专用目录（最稳但可能隐藏）", app_path))
    return choices


def app_settings_path():
    if platform == "android":
        try:
            from android.storage import app_storage_path

            return Path(app_storage_path()) / APP_SETTINGS_FILENAME
        except Exception:
            pass
    return Path.cwd() / APP_SETTINGS_FILENAME


def load_app_settings():
    try:
        path = app_settings_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_app_settings(settings):
    try:
        path = app_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        pass


def copy_android_content_uri(uri):
    uri = str(uri)
    if platform != "android" or not uri.startswith("content://"):
        return uri
    stream = None
    try:
        from jnius import autoclass, jarray

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Uri = autoclass("android.net.Uri")
        Intent = autoclass("android.content.Intent")
        OpenableColumns = autoclass("android.provider.OpenableColumns")

        activity = PythonActivity.mActivity
        resolver = activity.getContentResolver()
        parsed_uri = Uri.parse(uri)
        filename = "selected_file"
        try:
            resolver.takePersistableUriPermission(parsed_uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        except Exception:
            pass

        cursor = resolver.query(parsed_uri, None, None, None, None)
        if cursor:
            try:
                name_index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if name_index >= 0 and cursor.moveToFirst():
                    filename = cursor.getString(name_index) or filename
            finally:
                cursor.close()

        safe_name = safe_filename(filename)
        target = unique_path(tempfile.gettempdir(), safe_name)
        stream = resolver.openInputStream(parsed_uri)
        if stream is None:
            raise OSError("无法打开系统文件流")

        with open(target, "wb") as out:
            buffer = jarray("b")(64 * 1024)
            while True:
                count = stream.read(buffer)
                if count == -1:
                    break
                if count <= 0:
                    continue
                chunk = bytearray(count)
                for index in range(count):
                    chunk[index] = buffer[index] & 0xFF
                out.write(chunk)
        return str(target)
    except Exception as exc:
        raise OSError(f"无法读取系统选择的文件：{exc}") from exc
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


def prepare_android_content_fd(uri):
    if platform != "android":
        raise OSError("仅 Android 支持 content:// 文件描述符读取")
    from jnius import autoclass

    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    Uri = autoclass("android.net.Uri")
    Intent = autoclass("android.content.Intent")
    OpenableColumns = autoclass("android.provider.OpenableColumns")

    activity = PythonActivity.mActivity
    resolver = activity.getContentResolver()
    parsed_uri = Uri.parse(str(uri))
    filename = "selected_file"

    try:
        resolver.takePersistableUriPermission(parsed_uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
    except Exception:
        pass

    cursor = None
    try:
        cursor = resolver.query(parsed_uri, None, None, None, None)
        if cursor:
            name_index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if name_index >= 0 and cursor.moveToFirst():
                filename = cursor.getString(name_index) or filename
    except Exception:
        pass
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass

    target = unique_path(tempfile.gettempdir(), safe_filename(filename))
    pfd = resolver.openFileDescriptor(parsed_uri, "r")
    if pfd is None:
        raise OSError("系统没有提供可读取的文件描述符")
    fd = pfd.detachFd()
    if fd < 0:
        raise OSError("系统返回的文件描述符无效")
    return fd, str(target)


def android_scan_file(path):
    if platform != "android":
        return False
    try:
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        MediaScannerConnection = autoclass("android.media.MediaScannerConnection")
        activity = PythonActivity.mActivity
        try:
            MediaScannerConnection.scanFile(activity, [str(path)], None, None)
            return True
        except Exception:
            pass

        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        JavaFile = autoclass("java.io.File")
        intent = Intent(Intent.ACTION_MEDIA_SCANNER_SCAN_FILE)
        intent.setData(Uri.fromFile(JavaFile(str(path))))
        activity.sendBroadcast(intent)
        return True
    except Exception:
        return False


def android_scan_saved_path(path):
    if platform != "android":
        return False
    target = Path(path)
    paths = []
    if target.is_dir():
        for item in target.rglob("*"):
            if item.is_file():
                paths.append(item)
    elif target.exists():
        paths.append(target)
    scanned = False
    for item in paths:
        scanned = android_scan_file(item) or scanned
    return scanned


def android_path_may_be_hidden(path):
    normalized = str(path).replace("\\", "/")
    return platform == "android" and "/Android/data/" in normalized


def recvall(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("连接已经断开")
        data.extend(chunk)
    return bytes(data)


def send_json(sock, obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack("!I", len(body)) + body)


def recv_json(sock):
    header = recvall(sock, 4)
    size = struct.unpack("!I", header)[0]
    return json.loads(recvall(sock, size).decode("utf-8"))


def tune_transfer_socket(sock):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    for opt in (socket.SO_SNDBUF, socket.SO_RCVBUF):
        try:
            sock.setsockopt(socket.SOL_SOCKET, opt, 1024 * 1024)
        except OSError:
            pass
    return sock


def connect_tcp_with_retries(host, port, logger=None, attempts=SEND_CONNECT_ATTEMPTS):
    last_error = None
    for attempt in range(1, attempts + 1):
        sock = tune_transfer_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        sock.settimeout(SOCKET_TIMEOUT)
        try:
            sock.connect((host, port))
            return sock
        except OSError as exc:
            last_error = exc
            try:
                sock.close()
            except OSError:
                pass
            if attempt < attempts:
                if logger:
                    logger(f"连接失败（第 {attempt} 次）：{exc}，正在重试...")
                time.sleep(0.8)
    raise ConnectionError(f"无法连接 {host}:{port}：{last_error}")


def sha256_file(path, stop_event=None):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if stop_event and stop_event.is_set():
                raise InterruptedError("用户取消")
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest(), os.path.getsize(path)


def unique_path(folder, filename):
    folder = Path(folder)
    target = folder / filename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = folder / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def format_bytes(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def format_seconds(seconds):
    if seconds <= 0 or seconds == float("inf"):
        return "--"
    seconds = int(seconds)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}时{minutes}分"
    if minutes:
        return f"{minutes}分{sec}秒"
    return f"{sec}秒"


def get_local_ips():
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        default_ip = probe.getsockname()[0]
        probe.close()
        if default_ip not in ips and not default_ip.startswith("127."):
            ips.insert(0, default_ip)
    except Exception:
        pass

    return ips or ["127.0.0.1"]


class Card(BoxLayout):
    def __init__(
        self,
        bg_color=COLORS["surface"],
        border_color=COLORS["border"],
        radius=8,
        padding=14,
        spacing=10,
        **kwargs,
    ):
        super().__init__(padding=dp(padding), spacing=dp(spacing), **kwargs)
        self.bg_color = bg_color
        self.border_color = border_color
        self.radius = dp(radius)
        with self.canvas.before:
            Color(*self.bg_color)
            self._bg = RoundedRectangle(radius=[(self.radius, self.radius)] * 4)
        self.bind(pos=self._update_canvas, size=self._update_canvas)

    def _update_canvas(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size


class RoundedButton(Button):
    def __init__(
        self,
        bg_color=COLORS["primary"],
        text_color=(1, 1, 1, 1),
        radius=8,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.bg_color = bg_color
        self.color = text_color
        self.bold = True
        self.font_name = APP_FONT
        self.font_size = sp(14)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.radius = dp(radius)
        self.size_hint_y = None
        self.height = dp(kwargs.pop("height", 48))
        with self.canvas.before:
            Color(*self.bg_color)
            self._bg = RoundedRectangle(radius=[(self.radius, self.radius)] * 4)
        self.bind(pos=self._update_canvas, size=self._update_canvas)

    def _update_canvas(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size


class AppLabel(Label):
    def __init__(self, color=COLORS["text"], font_size_value=14, **kwargs):
        super().__init__(**kwargs)
        self.font_name = APP_FONT
        self.color = color
        self.font_size = sp(font_size_value)
        self.halign = kwargs.get("halign", "left")
        self.valign = kwargs.get("valign", "middle")
        self.bind(size=self._sync_text)

    def _sync_text(self, *_):
        self.text_size = (self.width, self.height if self.shorten else None)


class SectionTitle(AppLabel):
    def __init__(self, text, **kwargs):
        super().__init__(
            text=text,
            color=COLORS["primary_dark"],
            font_size_value=18,
            bold=True,
            size_hint_y=None,
            height=dp(30),
            **kwargs,
        )


class MutedLabel(AppLabel):
    def __init__(self, **kwargs):
        super().__init__(color=COLORS["muted"], font_size_value=13, **kwargs)


class Pill(Label):
    def __init__(self, text="", bg_color=COLORS["surface_soft"], color=COLORS["primary_dark"], **kwargs):
        super().__init__(text=text, color=color, bold=True, font_size=sp(12), **kwargs)
        self.font_name = APP_FONT
        self.size_hint_y = None
        self.height = dp(30)
        self.halign = "center"
        self.valign = "middle"
        self.bind(size=self._sync_text)
        with self.canvas.before:
            Color(*bg_color)
            self._bg = RoundedRectangle(radius=[(dp(8), dp(8))] * 4)
        self.bind(pos=self._update_canvas, size=self._update_canvas)

    def _sync_text(self, *_):
        self.text_size = self.size

    def _update_canvas(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size


class ModernTextInput(TextInput):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.multiline = False
        self.font_name = APP_FONT
        self.font_size = sp(15)
        self.foreground_color = COLORS["text"]
        self.background_color = (0.98, 0.99, 1.0, 1)
        self.cursor_color = COLORS["primary"]
        self.padding = [dp(12), dp(10), dp(12), dp(10)]
        self.size_hint_y = None
        self.height = dp(48)


class FileTransferApp(App):
    def build(self):
        Window.clearcolor = COLORS["bg"]
        request_android_storage_permissions()
        self.cancel_event = threading.Event()
        self.transfer_lock = threading.Lock()
        self.server_sock = None
        self.active_sock = None
        self.android_upload_server = None
        self.android_upload_thread = None
        self.selected_file = None
        self.settings = load_app_settings()
        configured_save_dir = self.settings.get("save_dir")
        self.save_dir = configured_save_dir or default_save_path()
        try:
            os.makedirs(self.save_dir, exist_ok=True)
        except OSError:
            self.save_dir = default_save_path()
            try:
                os.makedirs(self.save_dir, exist_ok=True)
            except OSError:
                pass
        self.temp_zip = None
        self.local_ips = get_local_ips()
        self.current_start_time = None
        self.current_start_offset = 0

        root = BoxLayout(orientation="vertical", padding=0, spacing=0)
        root.add_widget(self.build_header())

        scroll = ScrollView(do_scroll_x=False)
        content = BoxLayout(
            orientation="vertical",
            padding=[dp(14), dp(12), dp(14), dp(16)],
            spacing=dp(12),
            size_hint_y=None,
        )
        content.bind(minimum_height=content.setter("height"))

        content.add_widget(self.build_connection_card())
        content.add_widget(self.build_file_card())
        content.add_widget(self.build_transfer_card())
        content.add_widget(self.build_log_card())

        scroll.add_widget(content)
        root.add_widget(scroll)
        return root

    def build_header(self):
        header = BoxLayout(
            orientation="vertical",
            padding=[dp(16), dp(16), dp(16), dp(12)],
            spacing=dp(6),
            size_hint_y=None,
            height=dp(118),
        )
        with header.canvas.before:
            Color(*COLORS["primary_dark"])
            header._bg = RoundedRectangle(radius=[(0, 0), (0, 0), (dp(18), dp(18)), (dp(18), dp(18))])
        header.bind(pos=lambda obj, *_: setattr(obj._bg, "pos", obj.pos))
        header.bind(size=lambda obj, *_: setattr(obj._bg, "size", obj.size))

        top = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        title_box = BoxLayout(orientation="vertical", spacing=dp(2))
        title_box.add_widget(
            Label(
                text="北洋闪传",
                font_name=APP_FONT,
                color=(1, 1, 1, 1),
                bold=True,
                font_size=sp(24),
                halign="left",
                valign="middle",
            )
        )
        top.add_widget(title_box)
        top.add_widget(Pill(text=f"TCP {PORT}", bg_color=(1, 1, 1, 0.16), color=(1, 1, 1, 1), size_hint_x=0.28))
        header.add_widget(top)

        subtitle = Label(
            text="同网直传 · 断点续传 · SHA-256 校验",
            font_name=APP_FONT,
            color=(0.78, 0.87, 1, 1),
            font_size=sp(14),
            halign="left",
            valign="middle",
            size_hint_y=None,
            height=dp(24),
        )
        subtitle.bind(size=lambda obj, *_: setattr(obj, "text_size", obj.size))
        header.add_widget(subtitle)

        quick = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(8))
        quick.add_widget(Pill(text="接收方先监听", bg_color=(1, 1, 1, 0.12), color=(0.90, 0.96, 1, 1)))
        quick.add_widget(Pill(text="发送方输 IP", bg_color=(1, 1, 1, 0.12), color=(0.90, 0.96, 1, 1)))
        header.add_widget(quick)
        return header

    def build_connection_card(self):
        card = Card(orientation="vertical", size_hint_y=None)
        card.bind(minimum_height=card.setter("height"))
        card.add_widget(SectionTitle("1. 连接设备"))

        ip_text = " / ".join(self.local_ips)
        self.local_ip_label = AppLabel(
            text=f"我的 IP：{ip_text}",
            color=COLORS["primary_dark"],
            font_size_value=13,
            shorten=True,
            shorten_from="right",
            size_hint_y=None,
            height=dp(36),
        )
        card.add_widget(self.local_ip_label)

        row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        row.add_widget(Pill(text="对方 IP", bg_color=COLORS["cyan_soft"], size_hint_x=0.28))
        self.ip_input = ModernTextInput(
            text=self.settings.get("peer_ip", "127.0.0.1"),
            hint_text="例如 192.168.1.23",
        )
        self.ip_input.bind(text=self.on_peer_ip_changed)
        row.add_widget(self.ip_input)
        card.add_widget(row)

        actions = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(50))
        listen_btn = RoundedButton(text="开始接收", bg_color=COLORS["success"])
        listen_btn.bind(on_press=self.start_receiver)
        actions.add_widget(listen_btn)

        refresh_btn = RoundedButton(
            text="刷新 IP",
            bg_color=(0.90, 0.94, 0.99, 1),
            text_color=COLORS["primary_dark"],
        )
        refresh_btn.bind(on_press=self.refresh_local_ip)
        actions.add_widget(refresh_btn)
        card.add_widget(actions)

        note = MutedLabel(
            text="上次输入的对方 IP 会自动保存；两台设备需在同一 Wi-Fi、热点或不隔离的局域网内。",
            size_hint_y=None,
            height=dp(46),
        )
        card.add_widget(note)
        return card

    def build_file_card(self):
        card = Card(orientation="vertical", size_hint_y=None)
        card.bind(minimum_height=card.setter("height"))
        card.add_widget(SectionTitle("2. 文件与保存"))

        self.file_label = AppLabel(
            text="待发送文件：未选择",
            shorten=True,
            shorten_from="right",
            max_lines=1,
            size_hint_y=None,
            height=dp(38),
        )
        card.add_widget(self.file_label)

        self.save_label = AppLabel(
            text=f"保存位置：{self.save_dir}",
            color=COLORS["muted"],
            font_size_value=13,
            shorten=True,
            shorten_from="left",
            max_lines=1,
            size_hint_y=None,
            height=dp(38),
        )
        card.add_widget(self.save_label)

        actions = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(50))
        choose_file_btn = RoundedButton(text="选择文件")
        choose_file_btn.bind(on_press=lambda *_: self.open_file_picker())
        actions.add_widget(choose_file_btn)

        save_btn = RoundedButton(
            text="保存位置",
            bg_color=(0.90, 0.94, 0.99, 1),
            text_color=COLORS["primary_dark"],
        )
        save_btn.bind(on_press=lambda *_: self.open_folder_picker())
        actions.add_widget(save_btn)
        card.add_widget(actions)

        options = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(52))
        self.compress_box = CheckBox(size_hint_x=None, width=dp(42))
        self.unzip_box = CheckBox(active=True, size_hint_x=None, width=dp(42))
        options.add_widget(self.make_option("发送前压缩 ZIP", self.compress_box))
        options.add_widget(self.make_option("接收后自动解压", self.unzip_box))
        card.add_widget(options)

        save_hint = MutedLabel(
            text="手机端选择 PDF/Word 会打开文档安全选择页；选好后返回 App 即可发送。接收建议保存到“下载/北洋闪传”。",
            size_hint_y=None,
            height=dp(52),
        )
        card.add_widget(save_hint)
        return card

    def make_option(self, text, checkbox):
        box = Card(
            orientation="horizontal",
            bg_color=COLORS["surface_soft"],
            border_color=(0.90, 0.93, 0.97, 1),
            radius=8,
            padding=6,
            spacing=2,
        )
        box.add_widget(checkbox)
        box.add_widget(AppLabel(text=text, font_size_value=12))
        return box

    def build_transfer_card(self):
        card = Card(orientation="vertical", size_hint_y=None)
        card.bind(minimum_height=card.setter("height"))
        card.add_widget(SectionTitle("3. 传输监控"))

        self.status_label = AppLabel(
            text="准备就绪",
            bold=True,
            color=COLORS["primary_dark"],
            font_size_value=15,
            size_hint_y=None,
            height=dp(36),
        )
        card.add_widget(self.status_label)

        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(16))
        card.add_widget(self.progress)

        stats = GridLayout(cols=3, spacing=dp(8), size_hint_y=None, height=dp(64))
        self.percent_label = self.make_stat("进度", "0%")
        self.speed_label = self.make_stat("速度", "--")
        self.eta_label = self.make_stat("剩余", "--")
        stats.add_widget(self.percent_label)
        stats.add_widget(self.speed_label)
        stats.add_widget(self.eta_label)
        card.add_widget(stats)

        actions = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(50))
        send_btn = RoundedButton(text="立即发送", bg_color=COLORS["primary"])
        send_btn.bind(on_press=self.start_sender)
        actions.add_widget(send_btn)

        cancel_btn = RoundedButton(text="取消传输", bg_color=COLORS["danger"])
        cancel_btn.bind(on_press=self.cancel_transfer)
        actions.add_widget(cancel_btn)
        card.add_widget(actions)
        return card

    def make_stat(self, title, value):
        box = Card(
            orientation="vertical",
            bg_color=COLORS["surface_soft"],
            border_color=(0.90, 0.93, 0.97, 1),
            radius=8,
            padding=8,
            spacing=2,
        )
        box.title = MutedLabel(text=title, size_hint_y=None, height=dp(18), halign="center")
        box.value = AppLabel(
            text=value,
            color=COLORS["primary_dark"],
            bold=True,
            font_size_value=14,
            size_hint_y=None,
            height=dp(22),
            halign="center",
        )
        box.add_widget(box.title)
        box.add_widget(box.value)
        return box

    def build_log_card(self):
        card = Card(orientation="vertical", size_hint_y=None, height=dp(220))
        card.add_widget(SectionTitle("运行记录"))
        self.log_label = AppLabel(
            text="欢迎使用北洋闪传。请确保两台设备在同一个校园网或局域网内。",
            color=COLORS["muted"],
            font_size_value=13,
            size_hint_y=None,
            halign="left",
            valign="top",
        )
        self.log_label.bind(width=lambda obj, width: setattr(obj, "text_size", (width, None)))
        self.log_label.bind(texture_size=lambda obj, size: setattr(obj, "height", max(size[1] + dp(12), dp(150))))
        scroll = ScrollView(do_scroll_x=False)
        scroll.add_widget(self.log_label)
        card.add_widget(scroll)
        return card

    def refresh_local_ip(self, *_):
        self.local_ips = get_local_ips()
        self.local_ip_label.text = f"我的 IP：{' / '.join(self.local_ips)}"
        self.log("已刷新本机 IP。")

    def persist_setting(self, key, value):
        self.settings[key] = value
        save_app_settings(self.settings)

    def persist_peer_ip(self, value):
        value = (value or "").strip()
        if value:
            self.persist_setting("peer_ip", value)

    def on_peer_ip_changed(self, _instance, value):
        self.persist_peer_ip(value)

    def open_file_picker(self):
        if platform == "android":
            if self.open_android_safe_upload_picker():
                return
            self.log("文档安全选择页不可用，已改用内置稳定选择器。若看不到文档，请把文件放到 Download 或 Documents 后再选。")

        chooser = FileChooserIconView(path=default_file_path())
        popup = self.make_picker_popup("选择要发送的文件", chooser)

        def choose(*_):
            if chooser.selection:
                self.selected_file = chooser.selection[0]
                size_text = format_bytes(os.path.getsize(self.selected_file))
                self.file_label.text = f"待发送文件：{os.path.basename(self.selected_file)}  ({size_text})"
                self.log(f"已选择文件：{self.selected_file}")
                popup.dismiss()

        popup.ok_button.bind(on_press=choose)
        popup.open()

    def ensure_android_safe_upload_server(self):
        if self.android_upload_server:
            return self.android_upload_server.server_port
        last_error = None
        for port in (ANDROID_SAFE_UPLOAD_PORT, 50125, 50126, 50127):
            try:
                handler = create_android_safe_upload_handler(self)
                server = ThreadingHTTPServer(("127.0.0.1", port), handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.android_upload_server = server
                self.android_upload_thread = thread
                self.log(f"文档安全选择服务已启动：127.0.0.1:{server.server_port}")
                return server.server_port
            except OSError as exc:
                last_error = exc
        raise OSError(f"无法启动文档安全选择服务：{last_error}")

    def open_android_safe_upload_picker(self):
        try:
            port = self.ensure_android_safe_upload_server()
            url = f"http://127.0.0.1:{port}/"
            try:
                from jnius import autoclass

                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                Intent = autoclass("android.content.Intent")
                Uri = autoclass("android.net.Uri")
                activity = PythonActivity.mActivity
                intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
                activity.startActivity(intent)
            except Exception:
                webbrowser.open(url)
            self.log("已打开文档安全选择页。请选择 PDF/Word 后点“使用这个文件”，再回到 App 发送。")
            self.set_status("等待文档安全选择", COLORS["primary"])
            return True
        except Exception as exc:
            self.log(f"文档安全选择页不可用：{exc}")
            return False

    def open_android_document_picker(self):
        try:
            from android import activity as android_activity
            from jnius import autoclass

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Intent = autoclass("android.content.Intent")
            activity = PythonActivity.mActivity
            intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
            intent.addCategory(Intent.CATEGORY_OPENABLE)
            intent.setType("*/*")
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, False)
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            intent.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
            try:
                android_activity.unbind(on_activity_result=self.on_android_activity_result)
            except Exception:
                pass
            android_activity.bind(on_activity_result=self.on_android_activity_result)
            activity.startActivityForResult(intent, ANDROID_FILE_PICK_REQUEST)
            self.log("已打开系统文档选择器，可选择 PDF、文档、图片或压缩包。")
            return True
        except Exception as exc:
            self.log(f"原生文档选择器不可用，尝试兼容选择器：{exc}")
            return False

    def on_android_activity_result(self, request_code, result_code, intent):
        if request_code != ANDROID_FILE_PICK_REQUEST:
            return
        try:
            from android import activity as android_activity
            from jnius import autoclass

            try:
                android_activity.unbind(on_activity_result=self.on_android_activity_result)
            except Exception:
                pass

            Activity = autoclass("android.app.Activity")
            if result_code != Activity.RESULT_OK or intent is None:
                self.log("已取消选择文件。")
                return

            uri = None
            data = intent.getData()
            if data:
                uri = data.toString()
            else:
                clip_data = intent.getClipData()
                if clip_data and clip_data.getItemCount() > 0:
                    item = clip_data.getItemAt(0)
                    if item and item.getUri():
                        uri = item.getUri().toString()

            if not uri:
                self.on_file_selection_failed("系统没有返回可读取的文件地址")
                return
            self.on_native_file_selected([uri])
        except Exception as exc:
            self.on_file_selection_failed(f"文件选择回调失败：{exc}")

    def open_folder_picker(self):
        if platform == "android":
            self.open_android_save_picker()
            return

        chooser = FileChooserIconView(path=self.save_dir, dirselect=True)
        popup = self.make_picker_popup("选择接收保存目录", chooser)

        def choose(*_):
            selected = chooser.selection[0] if chooser.selection else chooser.path
            if os.path.isdir(selected):
                self.save_dir = selected
                self.save_label.text = f"保存位置：{self.save_dir}"
                self.persist_setting("save_dir", self.save_dir)
                self.log(f"保存位置已设置为：{self.save_dir}")
                popup.dismiss()

        popup.ok_button.bind(on_press=choose)
        popup.open()

    def open_android_save_picker(self):
        panel = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
        panel.add_widget(
            AppLabel(
                text="选择接收文件保存位置",
                bold=True,
                font_size_value=18,
                size_hint_y=None,
                height=dp(30),
            )
        )
        panel.add_widget(
            MutedLabel(
                text="推荐选择“下载/北洋闪传”，保存后会主动刷新文件管理器索引。",
                size_hint_y=None,
                height=dp(42),
            )
        )

        choices_box = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        choices_box.bind(minimum_height=choices_box.setter("height"))
        for label, path in android_save_choices():
            btn = RoundedButton(
                text=label,
                bg_color=(0.90, 0.94, 0.99, 1),
                text_color=COLORS["primary_dark"],
                height=48,
            )
            btn.bind(on_press=lambda _btn, selected=path: self.apply_save_dir(selected, popup))
            choices_box.add_widget(btn)

        choices_scroll = ScrollView(do_scroll_x=False, size_hint_y=1)
        choices_scroll.add_widget(choices_box)
        panel.add_widget(choices_scroll)

        manual_label = MutedLabel(text="手动路径", size_hint_y=None, height=dp(24))
        panel.add_widget(manual_label)
        manual_input = ModernTextInput(text=self.save_dir, hint_text="/storage/emulated/0/Download/北洋闪传")
        panel.add_widget(manual_input)

        button_row = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(48))
        cancel = RoundedButton(
            text="取消",
            bg_color=(0.90, 0.94, 0.99, 1),
            text_color=COLORS["primary_dark"],
        )
        ok = RoundedButton(text="使用此路径", bg_color=COLORS["primary"])
        button_row.add_widget(cancel)
        button_row.add_widget(ok)
        panel.add_widget(button_row)

        popup = Popup(
            title="保存位置",
            title_font=APP_FONT,
            title_size=sp(18),
            content=panel,
            size_hint=(0.90, 0.72),
        )
        cancel.bind(on_press=lambda *_: popup.dismiss())
        ok.bind(on_press=lambda *_: self.apply_save_dir(manual_input.text.strip(), popup))
        popup.open()

    def apply_save_dir(self, selected, popup=None):
        if not selected:
            self.log("保存路径不能为空。")
            return
        try:
            os.makedirs(selected, exist_ok=True)
            test_file = os.path.join(selected, ".beiyang_flash_write_test")
            with open(test_file, "wb") as f:
                f.write(b"ok")
            try:
                os.remove(test_file)
            except OSError:
                pass
        except Exception as exc:
            self.log(f"无法使用该保存位置：{selected}；原因：{exc}")
            self.set_status("保存位置不可写", COLORS["warning"])
            return

        self.save_dir = selected
        self.save_label.text = f"保存位置：{self.save_dir}"
        self.persist_setting("save_dir", self.save_dir)
        self.log(f"保存位置已设置为：{self.save_dir}")
        if popup:
            popup.dismiss()

    def ensure_receive_save_dir(self):
        if writable_directory(self.save_dir):
            return self.save_dir
        if platform == "android":
            fallback = android_app_external_files_path()
            if fallback and writable_directory(fallback):
                self.save_dir = fallback
                self.update_save_dir_label()
                self.log("原保存位置不可写，已临时切换到应用专用目录。若文件管理器看不到，请改选“下载/北洋闪传”并确认权限。")
                return self.save_dir
        raise OSError(f"保存位置不可写：{self.save_dir}")

    @mainthread
    def update_save_dir_label(self):
        self.save_label.text = f"保存位置：{self.save_dir}"

    @mainthread
    def on_native_file_selected(self, selection):
        try:
            if not selection:
                return
            selected = selection if isinstance(selection, str) else selection[0]
            selected = str(selected)
            self.set_status("正在读取所选文件", COLORS["primary"])
            self.log("正在读取所选文件，请稍候...")
            if platform == "android" and selected.startswith("content://"):
                try:
                    fd, target = prepare_android_content_fd(selected)
                except Exception as exc:
                    self.on_file_selection_failed(f"无法读取系统文档：{exc}")
                    return
                threading.Thread(target=self.copy_detached_fd_worker, args=(fd, target), daemon=True).start()
                return
            threading.Thread(target=self.resolve_selected_file_worker, args=(selected,), daemon=True).start()
        except Exception as exc:
            self.on_file_selection_failed(f"文件选择失败：{exc}")

    def copy_detached_fd_worker(self, fd, target):
        close_manually = True
        try:
            with os.fdopen(fd, "rb", closefd=True) as source, open(target, "wb") as out:
                close_manually = False
                while True:
                    chunk = source.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if not os.path.isfile(target) or os.path.getsize(target) <= 0:
                raise OSError("所选文档为空或无法复制")
            self.apply_selected_file(target, True)
        except Exception as exc:
            self.on_file_selection_failed(f"文档读取失败：{exc}")
        finally:
            if close_manually:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def resolve_selected_file_worker(self, selected):
        try:
            selected = str(selected)
            copied_from_uri = selected.startswith("content://")
            if copied_from_uri:
                raise OSError("文档 URI 未能准备为可读取文件，请改用 Download 或 Documents 内置选择器")
            if not os.path.isfile(selected):
                raise OSError(f"未能读取所选文件：{selected}")
            self.apply_selected_file(selected, copied_from_uri)
        except Exception as exc:
            self.on_file_selection_failed(str(exc))

    @mainthread
    def apply_selected_file(self, selected, copied_from_uri=False):
        self.selected_file = selected
        size_text = format_bytes(os.path.getsize(self.selected_file))
        self.file_label.text = f"待发送文件：{os.path.basename(self.selected_file)}  ({size_text})"
        self.set_status("文件已选择", COLORS["success"])
        if copied_from_uri:
            self.log("已读取系统文档选择器返回的文件。")
        self.log(f"已选择文件：{self.selected_file}")

    @mainthread
    def on_file_selection_failed(self, message):
        self.log(message)
        self.set_status("文件选择失败", COLORS["warning"])

    @mainthread
    def on_native_folder_selected(self, selection):
        if not selection:
            return
        selected = str(selection[0])
        if selected.startswith("content://"):
            self.log("系统返回的是 content:// 目录地址，已继续使用默认 Download 保存位置。")
            return
        if os.path.isdir(selected):
            self.save_dir = selected
            self.save_label.text = f"保存位置：{self.save_dir}"
            self.persist_setting("save_dir", self.save_dir)
            self.log(f"保存位置已设置为：{self.save_dir}")
        else:
            self.log(f"未能读取所选目录：{selected}")

    def make_picker_popup(self, title, chooser):
        panel = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        panel.add_widget(chooser)
        button_row = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(48))
        cancel = RoundedButton(
            text="取消",
            bg_color=(0.90, 0.94, 0.99, 1),
            text_color=COLORS["primary_dark"],
        )
        ok = RoundedButton(text="确定", bg_color=COLORS["primary"])
        button_row.add_widget(cancel)
        button_row.add_widget(ok)
        panel.add_widget(button_row)
        popup = Popup(
            title=title,
            title_font=APP_FONT,
            title_size=sp(18),
            content=panel,
            size_hint=(0.92, 0.9),
        )
        popup.ok_button = ok
        cancel.bind(on_press=lambda *_: popup.dismiss())
        return popup

    def start_receiver(self, *_):
        self.cancel_event.clear()
        threading.Thread(target=self.receiver_worker, daemon=True).start()

    def start_sender(self, *_):
        if not self.selected_file:
            self.log("请先选择要发送的文件。")
            self.set_status("请先选择文件", COLORS["warning"])
            return
        self.cancel_event.clear()
        threading.Thread(target=self.sender_worker, daemon=True).start()

    def cancel_transfer(self, *_):
        self.cancel_event.set()
        for sock in (self.active_sock, self.server_sock):
            try:
                if sock:
                    sock.shutdown(socket.SHUT_RDWR)
                    sock.close()
            except Exception:
                pass
        self.set_status("已取消，断点保留", COLORS["warning"])
        self.log("已发出取消请求。再次传输同一文件会尝试断点续传。")

    def receiver_worker(self):
        if not self.transfer_lock.acquire(blocking=False):
            self.log("已有传输正在进行，请先取消或等待完成。")
            return
        try:
            self.reset_transfer_ui()
            self.set_status(f"监听中 0.0.0.0:{PORT}", COLORS["primary"])
            self.log(f"正在监听 0.0.0.0:{PORT}，等待发送方连接...")
            self.server_sock = tune_transfer_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_sock.bind(("0.0.0.0", PORT))
            self.server_sock.listen(5)
            conn, addr = self.server_sock.accept()
            self.active_sock = tune_transfer_socket(conn)
            conn.settimeout(SOCKET_TIMEOUT)
            self.log(f"已连接发送方：{addr[0]}")

            meta = recv_json(conn)
            conn.settimeout(TRANSFER_TIMEOUT)
            filename = safe_filename(meta["filename"])
            total_size = int(meta["size"])
            digest = meta["sha256"]
            compressed = bool(meta.get("compressed"))

            receive_dir = self.ensure_receive_save_dir()
            final_path = unique_path(receive_dir, filename)
            part_path = Path(str(final_path) + ".part")
            state_path = Path(str(final_path) + ".state.json")
            offset = 0

            if part_path.exists() and state_path.exists():
                old = json.loads(state_path.read_text(encoding="utf-8"))
                if old.get("sha256") == digest and old.get("filename") == filename:
                    offset = min(part_path.stat().st_size, total_size)

            state_path.write_text(
                json.dumps(
                    {"filename": filename, "size": total_size, "sha256": digest},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            send_json(conn, {"ok": True, "offset": offset})

            bytes_received = offset
            mode = "ab" if offset else "wb"
            self.begin_meter(offset)
            self.log(f"开始接收：{filename}，从 {format_bytes(offset)} 处继续。")
            with open(part_path, mode) as f:
                while bytes_received < total_size:
                    if self.cancel_event.is_set():
                        raise InterruptedError("用户取消")
                    to_read = min(CHUNK_SIZE, total_size - bytes_received)
                    chunk = conn.recv(to_read)
                    if not chunk:
                        raise ConnectionError("连接中断，已保留断点文件")
                    f.write(chunk)
                    bytes_received += len(chunk)
                    self.show_progress(bytes_received, total_size, f"接收 {filename}")
                f.flush()
                os.fsync(f.fileno())

            self.set_status("正在校验 SHA-256", COLORS["primary"])
            self.log("正在校验文件签名...")
            actual_digest, _ = sha256_file(part_path, self.cancel_event)
            if actual_digest != digest:
                self.set_status("校验失败", COLORS["danger"])
                self.log("校验失败：文件可能损坏，请重新传输。")
                return

            part_path.replace(final_path)
            state_path.unlink(missing_ok=True)
            send_json(conn, {"done": True, "sha256": actual_digest})
            self.show_progress(total_size, total_size, f"完成 {filename}")
            self.set_status("接收完成，校验通过", COLORS["success"])
            self.log(f"接收完成并已保存：{final_path}")
            if android_scan_saved_path(final_path):
                self.log("已通知 Android 刷新文件索引，文件管理器应能更快看到。")
            if android_path_may_be_hidden(final_path):
                self.log("当前保存位置属于 Android/data，部分手机文件管理器会隐藏；建议改用“下载/北洋闪传”。")

            if compressed and self.unzip_box.active:
                extract_dir = self.unzip_received(final_path)
                if extract_dir and android_scan_saved_path(extract_dir):
                    self.log("已刷新解压目录的文件索引。")
        except InterruptedError:
            self.set_status("接收已取消", COLORS["warning"])
            self.log("接收已取消，断点文件已保留。")
        except Exception as exc:
            self.set_status("接收失败", COLORS["danger"])
            if isinstance(exc, socket.timeout):
                self.log(f"接收失败：{int(TRANSFER_TIMEOUT)} 秒内没有收到数据，已保留断点文件，可重新发送继续。")
            else:
                self.log(f"接收失败：{exc}")
        finally:
            self.close_sockets()
            self.transfer_lock.release()

    def sender_worker(self):
        if not self.transfer_lock.acquire(blocking=False):
            self.log("已有传输正在进行，请先取消或等待完成。")
            return
        send_path = self.selected_file
        try:
            self.reset_transfer_ui()
            if self.compress_box.active:
                self.set_status("正在压缩文件", COLORS["primary"])
                send_path = self.make_zip(self.selected_file)
                self.log(f"已生成压缩文件：{send_path}")

            self.set_status("正在生成 SHA-256", COLORS["primary"])
            self.log("正在生成文件签名...")
            digest, total_size = sha256_file(send_path, self.cancel_event)
            filename = os.path.basename(send_path)

            target_text = self.ip_input.text.strip()
            host, target_port = parse_target_address(target_text)
            self.persist_peer_ip(target_text)
            self.set_status(f"连接 {host}:{target_port}", COLORS["primary"])
            self.log(f"正在连接 {host}:{target_port} ...")
            sock = connect_tcp_with_retries(host, target_port, self.log)
            self.active_sock = sock

            send_json(
                sock,
                {
                    "op": "offer",
                    "filename": filename,
                    "size": total_size,
                    "sha256": digest,
                    "compressed": self.compress_box.active,
                    "chunk_size": CHUNK_SIZE,
                },
            )
            reply = recv_json(sock)
            if not reply.get("ok"):
                self.set_status("接收方拒绝", COLORS["danger"])
                self.log("接收方拒绝了文件。")
                return

            offset = int(reply.get("offset", 0))
            sock.settimeout(TRANSFER_TIMEOUT)
            self.begin_meter(offset)
            self.log(f"开始发送：{filename}，从 {format_bytes(offset)} 处继续。")
            bytes_sent = offset
            with open(send_path, "rb") as f:
                f.seek(offset)
                while bytes_sent < total_size:
                    if self.cancel_event.is_set():
                        raise InterruptedError("用户取消")
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    sock.sendall(chunk)
                    bytes_sent += len(chunk)
                    self.show_progress(bytes_sent, total_size, f"发送 {filename}")

            sock.settimeout(VERIFY_TIMEOUT)
            final_reply = recv_json(sock)
            if final_reply.get("sha256") == digest:
                self.show_progress(total_size, total_size, f"完成 {filename}")
                self.set_status("发送完成，校验通过", COLORS["success"])
                self.log("发送完成，接收方校验通过。")
            else:
                self.set_status("等待校验失败", COLORS["warning"])
                self.log("发送完成，但未收到接收方校验通过确认。")
        except InterruptedError:
            self.set_status("发送已取消", COLORS["warning"])
            self.log("发送已取消。")
        except Exception as exc:
            self.set_status("发送失败", COLORS["danger"])
            if isinstance(exc, socket.timeout):
                self.log(f"发送失败：网络长时间无响应。可重新点击发送，程序会尝试断点续传。")
            else:
                self.log(f"发送失败：{exc}")
            self.log(self.connection_help_text())
        finally:
            self.close_sockets()
            self.cleanup_temp_zip()
            self.transfer_lock.release()

    def connection_help_text(self):
        return (
            "连接提示：如果热点/家用路由器可以传、校园网不能传，通常是校园网开启了“客户端隔离”或阻止入站 TCP。"
            "请确认两台设备连接同一个 Wi‑Fi、关闭发送方移动数据干扰，输入接收方当前 Wi‑Fi IP；"
            "若仍失败，请用一台手机开热点，或连接同一个不隔离的局域网。"
        )

    def make_zip(self, file_path):
        source = Path(file_path)
        temp_dir = Path(tempfile.gettempdir())
        zip_path = temp_dir / f"{source.stem}_fast_transfer.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(source, arcname=source.name)
        self.temp_zip = str(zip_path)
        return str(zip_path)

    def unzip_received(self, zip_path):
        try:
            extract_dir = Path(zip_path).with_suffix("")
            extract_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            self.log(f"已自动解压到：{extract_dir}")
            return extract_dir
        except Exception as exc:
            self.log(f"自动解压失败：{exc}")
            return None

    def cleanup_temp_zip(self):
        if self.temp_zip:
            try:
                os.remove(self.temp_zip)
            except OSError:
                pass
            self.temp_zip = None

    def close_sockets(self):
        for attr in ("active_sock", "server_sock"):
            sock = getattr(self, attr, None)
            try:
                if sock:
                    sock.close()
            except Exception:
                pass
            setattr(self, attr, None)

    def on_stop(self):
        self.close_sockets()
        server = self.android_upload_server
        self.android_upload_server = None
        if server:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass

    @mainthread
    def begin_meter(self, offset):
        self.current_start_time = time.monotonic()
        self.current_start_offset = offset

    @mainthread
    def reset_transfer_ui(self):
        self.progress.value = 0
        self.percent_label.value.text = "0%"
        self.speed_label.value.text = "--"
        self.eta_label.value.text = "--"
        self.current_start_time = time.monotonic()
        self.current_start_offset = 0

    @mainthread
    def set_status(self, text, color):
        self.status_label.text = text
        self.status_label.color = color

    @mainthread
    def show_progress(self, done, total, label):
        pct = int(done * 100 / total) if total else 0
        elapsed = max(time.monotonic() - (self.current_start_time or time.monotonic()), 0.001)
        session_done = max(done - self.current_start_offset, 0)
        speed = session_done / elapsed
        eta = (total - done) / speed if speed > 1 else float("inf")
        self.progress.value = pct
        self.percent_label.value.text = f"{pct}%"
        self.speed_label.value.text = f"{format_bytes(speed)}/s"
        self.eta_label.value.text = format_seconds(eta)
        self.status_label.text = f"{label} · {format_bytes(done)} / {format_bytes(total)}"
        self.status_label.color = COLORS["primary_dark"]

    @mainthread
    def log(self, message):
        now = time.strftime("%H:%M:%S")
        lines = (self.log_label.text + f"\n[{now}] {message}").splitlines()
        self.log_label.text = "\n".join(lines[-80:])


def safe_filename(name):
    cleaned = os.path.basename((name or "").replace("\\", "/")).strip()
    if not cleaned or cleaned in {".", ".."}:
        cleaned = f"file_{int(time.time())}"
    return cleaned.replace("/", "_").replace("\\", "_")


ANDROID_SAFE_UPLOAD_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>北洋闪传 - 文档安全选择</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: system-ui, -apple-system, "Microsoft YaHei", sans-serif;
      color: #0b1526;
      background: #eef6ff;
      display: grid;
      place-items: center;
      padding: 18px;
    }
    main {
      width: min(100%, 560px);
      background: #fff;
      border: 1px solid #d8e4f2;
      border-radius: 10px;
      box-shadow: 0 12px 34px rgba(18, 69, 124, .13);
      overflow: hidden;
    }
    header {
      padding: 24px 22px;
      color: #fff;
      background: linear-gradient(135deg, #092f63, #0f73df);
    }
    h1 { margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }
    p { margin: 0; line-height: 1.7; color: #667589; }
    header p { color: rgba(255,255,255,.82); }
    section { padding: 22px; }
    input[type=file] {
      width: 100%;
      min-height: 54px;
      padding: 14px;
      border: 2px dashed #abc5df;
      border-radius: 8px;
      background: #f7faff;
      font: inherit;
    }
    button {
      width: 100%;
      min-height: 50px;
      margin-top: 14px;
      border: 0;
      border-radius: 8px;
      color: #fff;
      background: #0f73df;
      font: inherit;
      font-weight: 800;
    }
    button:disabled { opacity: .55; }
    .status {
      margin-top: 14px;
      padding: 12px;
      border-radius: 8px;
      background: #eef6ff;
      color: #092f63;
      min-height: 48px;
      line-height: 1.6;
      word-break: break-word;
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>北洋闪传</h1>
      <p>文档安全选择页</p>
    </header>
    <section>
      <p>请选择 PDF、Word、压缩包或其他文档。选择后会直接传回北洋闪传 App，不经过 Kivy 原生文档读取路径。</p>
      <input id="file" type="file">
      <button id="upload" disabled>使用这个文件</button>
      <div id="status" class="status">等待选择文件。</div>
    </section>
  </main>
  <script>
    const fileInput = document.getElementById("file");
    const upload = document.getElementById("upload");
    const statusBox = document.getElementById("status");
    let currentFile = null;
    function fmt(bytes) {
      const units = ["B", "KB", "MB", "GB"];
      let value = bytes;
      let index = 0;
      while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index += 1;
      }
      return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
    }
    fileInput.addEventListener("change", () => {
      currentFile = fileInput.files && fileInput.files[0];
      upload.disabled = !currentFile;
      statusBox.textContent = currentFile ? `已选择：${currentFile.name}（${fmt(currentFile.size)}）` : "等待选择文件。";
    });
    upload.addEventListener("click", async () => {
      if (!currentFile) return;
      upload.disabled = true;
      statusBox.textContent = "正在交给北洋闪传 App，请稍候...";
      try {
        const url = `/upload?filename=${encodeURIComponent(currentFile.name)}&size=${currentFile.size}`;
        const response = await fetch(url, {method: "POST", body: currentFile});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `上传失败：${response.status}`);
        statusBox.textContent = `已交给北洋闪传：${data.file}。现在可以返回 App 点击“立即发送”。`;
      } catch (error) {
        statusBox.textContent = error.message;
        upload.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


def create_android_safe_upload_handler(app):
    class AndroidSafeUploadHandler(BaseHTTPRequestHandler):
        server_version = "BeiyangFlashAndroidPicker/1.0"

        def log_message(self, *_):
            return

        def send_bytes(self, body, content_type="text/plain; charset=utf-8", status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, payload, status=200):
            self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                self.send_bytes(ANDROID_SAFE_UPLOAD_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            self.send_json({"error": "页面不存在"}, 404)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path != "/upload":
                    self.send_json({"error": "接口不存在"}, 404)
                    return
                params = urllib.parse.parse_qs(parsed.query)
                filename = safe_filename((params.get("filename") or ["selected_file"])[0])
                expected_size = int((params.get("size") or ["0"])[0])
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0:
                    raise ValueError("浏览器没有传回文件内容")
                staging_dir = Path(tempfile.gettempdir()) / "beiyang_flash_android_uploads"
                staging_dir.mkdir(parents=True, exist_ok=True)
                target = unique_path(staging_dir, filename)
                received = 0
                with open(target, "wb") as out:
                    while received < length:
                        chunk = self.rfile.read(min(WEB_CHUNK_SIZE, length - received))
                        if not chunk:
                            break
                        out.write(chunk)
                        received += len(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                if expected_size and received != expected_size:
                    raise ValueError("读取文件不完整，请重新选择")
                app.apply_selected_file(str(target), False)
                app.log(f"已通过文档安全选择页读取文件：{filename} ({format_bytes(received)})")
                self.send_json({"ok": True, "file": filename, "size": received})
            except Exception as exc:
                app.log(f"文档安全选择失败：{exc}")
                self.send_json({"error": str(exc)}, 400)

    return AndroidSafeUploadHandler


def test_writable_dir(folder):
    path = Path(folder).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    test_file = path / ".beiyang_flash_write_test"
    test_file.write_bytes(b"ok")
    try:
        test_file.unlink()
    except OSError:
        pass
    return str(path)


def unique_dir(folder):
    target = Path(folder)
    if not target.exists():
        return target
    index = 1
    while True:
        candidate = Path(f"{target}_{index}")
        if not candidate.exists():
            return candidate
        index += 1


class WebTransferState:
    def __init__(self):
        self.lock = threading.Lock()
        self.save_dir = None
        for candidate in (
            Path.home() / "Downloads" / "北洋闪传",
            Path.cwd() / "received" / "北洋闪传",
            Path(tempfile.gettempdir()) / "北洋闪传",
        ):
            try:
                self.save_dir = test_writable_dir(candidate)
                break
            except OSError:
                continue
        if self.save_dir is None:
            raise OSError("没有找到可写的默认保存目录")
        self.uploads = {}
        self.events = []

    def log(self, message):
        now = time.strftime("%H:%M:%S")
        with self.lock:
            self.events.append(f"[{now}] {message}")
            self.events = self.events[-80:]


WEB_APP_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>北洋闪传 Web</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f8fc;
      --panel: #ffffff;
      --panel-soft: #f5f9ff;
      --ink: #0d1726;
      --muted: #657386;
      --line: #d9e4f2;
      --primary: #0f73df;
      --primary-dark: #092c5c;
      --success: #0b8a54;
      --danger: #d72b35;
      --shadow: 0 18px 50px rgba(16, 57, 106, .14);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
      background:
        linear-gradient(180deg, rgba(14, 93, 181, .10), transparent 280px),
        repeating-linear-gradient(135deg, rgba(15, 115, 223, .055) 0 1px, transparent 1px 18px),
        var(--bg);
      color: var(--ink);
    }
    .app {
      width: min(1180px, calc(100vw - 32px));
      margin: 24px auto 44px;
    }
    header {
      background: linear-gradient(135deg, #092c5c, #0f73df);
      border-radius: 8px;
      color: #fff;
      min-height: 184px;
      padding: 28px;
      box-shadow: var(--shadow);
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 24px;
      align-items: center;
      overflow: hidden;
    }
    .brand h1 {
      margin: 0 0 10px;
      font-size: clamp(34px, 5vw, 56px);
      font-weight: 800;
      letter-spacing: 0;
    }
    .brand p {
      margin: 0;
      color: rgba(255, 255, 255, .82);
      font-size: 16px;
      line-height: 1.7;
    }
    .signal {
      width: min(340px, 34vw);
      height: 120px;
      border: 1px solid rgba(255, 255, 255, .26);
      border-radius: 8px;
      background:
        linear-gradient(90deg, transparent 0 10%, rgba(255,255,255,.18) 10% 11%, transparent 11% 30%, rgba(255,255,255,.18) 30% 31%, transparent 31% 54%, rgba(255,255,255,.18) 54% 55%, transparent 55%),
        linear-gradient(180deg, rgba(255,255,255,.16), rgba(255,255,255,.04));
      position: relative;
    }
    .signal::before,
    .signal::after {
      content: "";
      position: absolute;
      left: 28px;
      right: 28px;
      height: 2px;
      background: rgba(255,255,255,.42);
      border-radius: 999px;
    }
    .signal::before { top: 42px; }
    .signal::after { top: 76px; }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      margin-top: 18px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 22px;
    }
    .wide { grid-column: 1 / -1; }
    h2 {
      margin: 0 0 16px;
      font-size: 21px;
      letter-spacing: 0;
    }
    label {
      display: block;
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 8px;
    }
    input[type="text"] {
      width: 100%;
      height: 46px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 14px;
      color: var(--ink);
      background: #fbfdff;
      font: inherit;
      outline: none;
    }
    input[type="text"]:focus {
      border-color: rgba(15, 115, 223, .7);
      box-shadow: 0 0 0 4px rgba(15, 115, 223, .12);
    }
    .row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    button {
      border: 0;
      border-radius: 8px;
      min-height: 46px;
      padding: 0 18px;
      background: var(--primary);
      color: #fff;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button.secondary {
      background: #e9f1fb;
      color: var(--primary-dark);
    }
    button.danger {
      background: var(--danger);
    }
    button:disabled {
      opacity: .55;
      cursor: not-allowed;
    }
    .drop {
      min-height: 186px;
      border: 2px dashed #aac4df;
      border-radius: 8px;
      background: var(--panel-soft);
      display: grid;
      place-items: center;
      text-align: center;
      padding: 24px;
      transition: .18s ease;
    }
    .drop.active {
      border-color: var(--primary);
      background: #eaf4ff;
    }
    .drop strong {
      display: block;
      font-size: 20px;
      margin-bottom: 8px;
    }
    .drop span {
      color: var(--muted);
      line-height: 1.7;
    }
    .file-input {
      display: none;
    }
    .checks {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 14px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 10px;
      background: var(--panel-soft);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      color: var(--ink);
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin: 16px 0;
    }
    .stat {
      background: var(--panel-soft);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }
    .stat span {
      display: block;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }
    .stat strong {
      font-size: 18px;
      color: var(--primary-dark);
    }
    progress {
      width: 100%;
      height: 14px;
      appearance: none;
    }
    progress::-webkit-progress-bar {
      background: #dfe9f5;
      border-radius: 999px;
    }
    progress::-webkit-progress-value {
      background: linear-gradient(90deg, #0f73df, #18a8ff);
      border-radius: 999px;
    }
    .status {
      color: var(--primary-dark);
      font-weight: 800;
      min-height: 28px;
      margin-top: 10px;
    }
    .links {
      display: grid;
      gap: 8px;
    }
    .link-pill {
      background: #eff6ff;
      color: var(--primary-dark);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      overflow-wrap: anywhere;
      font-family: Consolas, "Microsoft YaHei", monospace;
    }
    pre {
      white-space: pre-wrap;
      min-height: 160px;
      max-height: 260px;
      overflow: auto;
      margin: 0;
      padding: 14px;
      background: #08192f;
      color: #d7eaff;
      border-radius: 8px;
      line-height: 1.65;
    }
    .hint {
      color: var(--muted);
      line-height: 1.7;
      margin: 10px 0 0;
    }
    @media (max-width: 820px) {
      .app { width: min(100vw - 18px, 720px); margin-top: 10px; }
      header { grid-template-columns: 1fr; min-height: 160px; padding: 22px; }
      .signal { display: none; }
      .grid { grid-template-columns: 1fr; }
      .stepbar { grid-template-columns: 1fr 1fr; }
      .checks, .stats { grid-template-columns: 1fr; }
      button { width: 100%; }
    }
  </style>
</head>
<body>
  <main class="app">
    <header>
      <section class="brand">
        <h1>北洋闪传</h1>
        <p>网页端文件速传 · 分块上传 · 断点续传 · SHA-256 校验</p>
      </section>
      <div class="signal" aria-hidden="true"></div>
    </header>

    <section class="grid">
      <div class="card">
        <h2>接收端</h2>
        <label for="saveDir">电脑保存目录</label>
        <div class="row">
          <input id="saveDir" type="text" autocomplete="off">
          <button id="saveBtn" class="secondary">设置保存位置</button>
        </div>
        <p class="hint">浏览器不能直接替 Python 选择系统文件夹，所以这里填写的是接收电脑上的目录。</p>
      </div>

      <div class="card">
        <h2>访问地址</h2>
        <div id="links" class="links"></div>
      </div>

      <div class="card wide">
        <h2>选择并发送文件</h2>
        <input id="fileInput" class="file-input" type="file">
        <div id="drop" class="drop">
          <div>
            <strong id="fileTitle">点击或拖入文件</strong>
            <span id="fileMeta">发送方打开此网页后选择文件，接收电脑会保存到上方目录。</span>
          </div>
        </div>
        <div class="checks">
          <label class="check"><input id="zipBefore" type="checkbox"> 发送前压缩 ZIP</label>
          <label class="check"><input id="autoUnzip" type="checkbox" checked> 接收后自动解压 ZIP</label>
        </div>
      </div>

      <div class="card wide">
        <h2>传输监控</h2>
        <div class="status" id="status">等待选择文件</div>
        <progress id="progress" value="0" max="100"></progress>
        <div class="stats">
          <div class="stat"><span>进度</span><strong id="percent">0%</strong></div>
          <div class="stat"><span>速度</span><strong id="speed">--</strong></div>
          <div class="stat"><span>剩余</span><strong id="eta">--</strong></div>
          <div class="stat"><span>校验</span><strong id="hashState">--</strong></div>
        </div>
        <div class="row">
          <button id="sendBtn" disabled>立即发送</button>
          <button id="clearBtn" class="danger">清空状态</button>
        </div>
      </div>

      <div class="card wide">
        <h2>运行记录</h2>
        <pre id="log">网页端已就绪。</pre>
      </div>
    </section>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    const drop = $("drop");
    const fileInput = $("fileInput");
    const sendBtn = $("sendBtn");
    let selectedFile = null;
    let sending = false;

    function log(message) {
      const now = new Date().toLocaleTimeString();
      $("log").textContent = ($("log").textContent + `\n[${now}] ${message}`).split("\n").slice(-80).join("\n");
      $("log").scrollTop = $("log").scrollHeight;
    }

    function fmt(bytes) {
      if (!Number.isFinite(bytes)) return "--";
      const units = ["B", "KB", "MB", "GB", "TB"];
      let value = bytes;
      let index = 0;
      while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index++;
      }
      return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
    }

    function fmtTime(seconds) {
      if (!Number.isFinite(seconds) || seconds < 0) return "--";
      if (seconds < 1) return "马上";
      const m = Math.floor(seconds / 60);
      const s = Math.floor(seconds % 60);
      return m ? `${m}分${s}秒` : `${s}秒`;
    }

    function setProgress(done, total, startedAt, startOffset) {
      const pct = total ? Math.floor(done * 100 / total) : 0;
      const elapsed = Math.max((performance.now() - startedAt) / 1000, .001);
      const speed = Math.max(done - startOffset, 0) / elapsed;
      $("progress").value = pct;
      $("percent").textContent = `${pct}%`;
      $("speed").textContent = `${fmt(speed)}/s`;
      $("eta").textContent = speed > 1 ? fmtTime((total - done) / speed) : "--";
    }

    async function api(path, options = {}) {
      const response = await fetch(path, options);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || `请求失败：${response.status}`);
      }
      return data;
    }

    async function loadInfo() {
      const info = await api("/api/info");
      $("saveDir").value = info.save_dir;
      $("links").innerHTML = info.urls.map(url => `<div class="link-pill">${url}</div>`).join("");
      log(`服务已启动，端口 ${info.port}`);
    }

    $("saveBtn").addEventListener("click", async () => {
      try {
        const data = await api("/api/config", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({save_dir: $("saveDir").value})
        });
        $("saveDir").value = data.save_dir;
        log(`保存位置已设置为：${data.save_dir}`);
      } catch (error) {
        log(error.message);
      }
    });

    function chooseFile(file) {
      selectedFile = file;
      $("fileTitle").textContent = file.name;
      $("fileMeta").textContent = `${fmt(file.size)} · ${file.type || "未知类型"}`;
      sendBtn.disabled = false;
      log(`已选择文件：${file.name}`);
    }

    drop.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      if (fileInput.files[0]) chooseFile(fileInput.files[0]);
    });
    ["dragenter", "dragover"].forEach(name => drop.addEventListener(name, event => {
      event.preventDefault();
      drop.classList.add("active");
    }));
    ["dragleave", "drop"].forEach(name => drop.addEventListener(name, event => {
      event.preventDefault();
      drop.classList.remove("active");
    }));
    drop.addEventListener("drop", event => {
      const file = event.dataTransfer.files[0];
      if (file) chooseFile(file);
    });

    function crc32(bytes) {
      let table = crc32.table;
      if (!table) {
        table = new Uint32Array(256);
        for (let i = 0; i < 256; i++) {
          let c = i;
          for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
          table[i] = c >>> 0;
        }
        crc32.table = table;
      }
      let c = 0xffffffff;
      for (const byte of bytes) c = table[(c ^ byte) & 0xff] ^ (c >>> 8);
      return (c ^ 0xffffffff) >>> 0;
    }

    function u16(view, offset, value) { view.setUint16(offset, value, true); }
    function u32(view, offset, value) { view.setUint32(offset, value >>> 0, true); }

    async function makeZipBlob(file) {
      const original = new Uint8Array(await file.arrayBuffer());
      const nameBytes = new TextEncoder().encode(file.name);
      const crc = crc32(original);
      let method = 0;
      let payload = original;

      if ("CompressionStream" in window) {
        const compressed = await new Response(new Blob([original]).stream().pipeThrough(new CompressionStream("deflate-raw"))).arrayBuffer();
        payload = new Uint8Array(compressed);
        method = 8;
      } else {
        log("当前浏览器不支持原生压缩，将使用 ZIP 存储模式。");
      }

      const local = new Uint8Array(30 + nameBytes.length);
      const lv = new DataView(local.buffer);
      u32(lv, 0, 0x04034b50); u16(lv, 4, 20); u16(lv, 6, 0x0800); u16(lv, 8, method);
      u16(lv, 10, 0); u16(lv, 12, 0); u32(lv, 14, crc); u32(lv, 18, payload.length); u32(lv, 22, original.length);
      u16(lv, 26, nameBytes.length); u16(lv, 28, 0); local.set(nameBytes, 30);

      const central = new Uint8Array(46 + nameBytes.length);
      const cv = new DataView(central.buffer);
      u32(cv, 0, 0x02014b50); u16(cv, 4, 20); u16(cv, 6, 20); u16(cv, 8, 0x0800); u16(cv, 10, method);
      u16(cv, 12, 0); u16(cv, 14, 0); u32(cv, 16, crc); u32(cv, 20, payload.length); u32(cv, 24, original.length);
      u16(cv, 28, nameBytes.length); u16(cv, 30, 0); u16(cv, 32, 0); u16(cv, 34, 0); u16(cv, 36, 0);
      u32(cv, 38, 0); u32(cv, 42, 0); central.set(nameBytes, 46);

      const end = new Uint8Array(22);
      const ev = new DataView(end.buffer);
      u32(ev, 0, 0x06054b50); u16(ev, 4, 0); u16(ev, 6, 0); u16(ev, 8, 1); u16(ev, 10, 1);
      u32(ev, 12, central.length); u32(ev, 16, local.length + payload.length); u16(ev, 20, 0);

      return new Blob([local, payload, central, end], {type: "application/zip"});
    }

    async function sha256Hex(blob) {
      const buffer = await blob.arrayBuffer();
      const hash = await crypto.subtle.digest("SHA-256", buffer);
      return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, "0")).join("");
    }

    sendBtn.addEventListener("click", async () => {
      if (!selectedFile || sending) return;
      sending = true;
      sendBtn.disabled = true;
      $("hashState").textContent = "计算中";
      $("status").textContent = "准备文件";
      try {
        let uploadBlob = selectedFile;
        let filename = selectedFile.name;
        const compressed = $("zipBefore").checked;
        if (compressed) {
          $("status").textContent = "正在压缩 ZIP";
          uploadBlob = await makeZipBlob(selectedFile);
          filename = selectedFile.name.replace(/\.[^/.]+$/, "") + "_fast_transfer.zip";
          log(`已生成 ZIP：${filename}，${fmt(uploadBlob.size)}`);
        }

        $("status").textContent = "正在计算 SHA-256";
        const digest = await sha256Hex(uploadBlob);
        $("hashState").textContent = "已生成";
        log(`SHA-256：${digest}`);

        const start = await api("/api/upload/start", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            filename,
            size: uploadBlob.size,
            sha256: digest,
            compressed,
            auto_unzip: $("autoUnzip").checked
          })
        });

        let offset = start.offset || 0;
        const startedAt = performance.now();
        const startOffset = offset;
        if (offset) log(`检测到断点，从 ${fmt(offset)} 继续。`);
        $("status").textContent = "正在上传";

        while (offset < uploadBlob.size) {
          const chunk = uploadBlob.slice(offset, Math.min(offset + start.chunk_size, uploadBlob.size));
          const response = await fetch(`/api/upload/chunk?upload_id=${encodeURIComponent(start.upload_id)}&offset=${offset}`, {
            method: "POST",
            headers: {"Content-Type": "application/octet-stream"},
            body: chunk
          });
          const data = await response.json();
          if (response.status === 409 && Number.isFinite(data.expected)) {
            offset = data.expected;
            log(`断点位置已校准到 ${fmt(offset)}。`);
            continue;
          }
          if (!response.ok) throw new Error(data.error || "上传分块失败");
          offset = data.received;
          setProgress(offset, uploadBlob.size, startedAt, startOffset);
        }

        $("status").textContent = "正在校验并保存";
        const done = await api("/api/upload/finish", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({upload_id: start.upload_id})
        });
        setProgress(uploadBlob.size, uploadBlob.size, startedAt, startOffset);
        $("hashState").textContent = "通过";
        $("status").textContent = "传输完成";
        log(`接收完成：${done.path}`);
        if (done.extract_dir) log(`已自动解压到：${done.extract_dir}`);
      } catch (error) {
        $("status").textContent = "传输失败";
        log(error.message);
      } finally {
        sending = false;
        sendBtn.disabled = !selectedFile;
      }
    });

    $("clearBtn").addEventListener("click", () => {
      $("progress").value = 0;
      $("percent").textContent = "0%";
      $("speed").textContent = "--";
      $("eta").textContent = "--";
      $("hashState").textContent = "--";
      $("status").textContent = selectedFile ? "等待发送" : "等待选择文件";
      $("log").textContent = "状态已清空。";
    });

    loadInfo().catch(error => log(error.message));
  </script>
</body>
</html>
"""


def create_web_handler(state, port):
    class WebTransferHandler(BaseHTTPRequestHandler):
        server_version = "BeiyangFlashWeb/1.0"

        def log_message(self, *_):
            return

        def send_json_response(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_json_body(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                body = WEB_APP_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/info":
                ips = get_local_ips()
                urls = [f"http://{ip}:{port}" for ip in ips]
                self.send_json_response(
                    {
                        "app": "北洋闪传 Web",
                        "port": port,
                        "ips": ips,
                        "urls": urls,
                        "save_dir": state.save_dir,
                        "chunk_size": WEB_CHUNK_SIZE,
                    }
                )
                return
            self.send_json_response({"error": "页面不存在"}, 404)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path == "/api/config":
                    data = self.read_json_body()
                    selected = (data.get("save_dir") or "").strip()
                    if not selected:
                        raise ValueError("保存目录不能为空")
                    save_dir = test_writable_dir(selected)
                    with state.lock:
                        state.save_dir = save_dir
                    state.log(f"保存位置已设置为：{save_dir}")
                    self.send_json_response({"ok": True, "save_dir": save_dir})
                    return

                if parsed.path == "/api/upload/start":
                    data = self.read_json_body()
                    filename = safe_filename(data.get("filename"))
                    total_size = int(data.get("size") or 0)
                    digest = (data.get("sha256") or "").strip().lower()
                    if total_size <= 0:
                        raise ValueError("文件大小无效")
                    if len(digest) != 64:
                        raise ValueError("SHA-256 签名无效")

                    with state.lock:
                        save_dir = Path(state.save_dir)
                        save_dir.mkdir(parents=True, exist_ok=True)
                        final_path = save_dir / filename
                        part_path = Path(str(final_path) + ".part")
                        state_path = Path(str(final_path) + ".state.json")
                        offset = 0

                        if part_path.exists() and state_path.exists():
                            try:
                                old = json.loads(state_path.read_text(encoding="utf-8"))
                                if old.get("sha256") == digest and old.get("filename") == filename:
                                    offset = min(part_path.stat().st_size, total_size)
                                else:
                                    final_path = unique_path(save_dir, filename)
                                    part_path = Path(str(final_path) + ".part")
                                    state_path = Path(str(final_path) + ".state.json")
                            except Exception:
                                final_path = unique_path(save_dir, filename)
                                part_path = Path(str(final_path) + ".part")
                                state_path = Path(str(final_path) + ".state.json")
                        elif final_path.exists():
                            final_path = unique_path(save_dir, filename)
                            part_path = Path(str(final_path) + ".part")
                            state_path = Path(str(final_path) + ".state.json")

                        upload_id = hashlib.sha1(
                            f"{filename}|{digest}|{time.time()}".encode("utf-8")
                        ).hexdigest()
                        state.uploads[upload_id] = {
                            "filename": filename,
                            "size": total_size,
                            "sha256": digest,
                            "final_path": str(final_path),
                            "part_path": str(part_path),
                            "state_path": str(state_path),
                            "compressed": bool(data.get("compressed")),
                            "auto_unzip": bool(data.get("auto_unzip", True)),
                        }
                        state_path.write_text(
                            json.dumps(
                                {"filename": filename, "size": total_size, "sha256": digest},
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                    state.log(f"准备接收：{filename}，断点 {format_bytes(offset)}")
                    self.send_json_response(
                        {"ok": True, "upload_id": upload_id, "offset": offset, "chunk_size": WEB_CHUNK_SIZE}
                    )
                    return

                if parsed.path == "/api/upload/chunk":
                    params = urllib.parse.parse_qs(parsed.query)
                    upload_id = (params.get("upload_id") or [""])[0]
                    offset = int((params.get("offset") or ["0"])[0])
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    if not upload_id or length < 0:
                        raise ValueError("分块参数无效")

                    with state.lock:
                        upload = state.uploads.get(upload_id)
                        if not upload:
                            raise ValueError("传输会话不存在")
                        part_path = Path(upload["part_path"])
                        expected = part_path.stat().st_size if part_path.exists() else 0
                        if offset != expected:
                            self.send_json_response({"error": "断点不一致", "expected": expected}, 409)
                            return
                        chunk = self.rfile.read(length)
                        with open(part_path, "ab") as f:
                            f.write(chunk)
                        received = expected + len(chunk)
                    self.send_json_response({"ok": True, "received": received})
                    return

                if parsed.path == "/api/upload/finish":
                    data = self.read_json_body()
                    upload_id = data.get("upload_id")
                    with state.lock:
                        upload = state.uploads.get(upload_id)
                    if not upload:
                        raise ValueError("传输会话不存在")

                    final_path = Path(upload["final_path"])
                    part_path = Path(upload["part_path"])
                    state_path = Path(upload["state_path"])
                    if not part_path.exists() or part_path.stat().st_size != int(upload["size"]):
                        raise ValueError("文件尚未接收完整")

                    actual_digest, _ = sha256_file(part_path)
                    if actual_digest != upload["sha256"]:
                        raise ValueError("SHA-256 校验失败，请重新传输")

                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    part_path.replace(final_path)
                    state_path.unlink(missing_ok=True)
                    extract_dir = ""
                    if upload.get("auto_unzip") and zipfile.is_zipfile(final_path):
                        extract_dir_path = unique_dir(final_path.with_suffix(""))
                        extract_dir_path.mkdir(parents=True, exist_ok=True)
                        with zipfile.ZipFile(final_path, "r") as zf:
                            zf.extractall(extract_dir_path)
                        extract_dir = str(extract_dir_path)

                    with state.lock:
                        state.uploads.pop(upload_id, None)
                    state.log(f"接收完成并校验通过：{final_path}")
                    self.send_json_response(
                        {
                            "ok": True,
                            "path": str(final_path),
                            "sha256": actual_digest,
                            "extract_dir": extract_dir,
                        }
                    )
                    return

                self.send_json_response({"error": "接口不存在"}, 404)
            except Exception as exc:
                state.log(f"请求失败：{exc}")
                self.send_json_response({"error": str(exc)}, 400)

    return WebTransferHandler


def run_web_app(open_browser=True):
    state = WebTransferState()
    last_error = None
    for port in [PORT, 8000, 8001, 8010, 8080]:
        try:
            handler = create_web_handler(state, port)
            server = ThreadingHTTPServer(("0.0.0.0", port), handler)
            break
        except OSError as exc:
            last_error = exc
    else:
        raise OSError(f"无法启动网页服务：{last_error}")

    urls = [f"http://{ip}:{server.server_port}" for ip in get_local_ips()]
    local_url = f"http://127.0.0.1:{server.server_port}"
    print("北洋闪传 Web 已启动")
    print(f"本机打开：{local_url}")
    print("同一局域网设备打开：")
    for url in urls:
        print(f"  {url}")
    print(f"保存目录：{state.save_dir}")
    print("按 Ctrl+C 停止服务")
    if open_browser:
        try:
            webbrowser.open(local_url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止北洋闪传 Web")
    finally:
        server.server_close()


DESKTOP_WEB_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>北洋闪传</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef6ff;
      --panel: #ffffff;
      --soft: #f7faff;
      --ink: #0b1526;
      --muted: #667589;
      --line: #d8e4f2;
      --blue: #0f73df;
      --blue-dark: #092f63;
      --green: #0b8a54;
      --red: #dc2f3a;
      --cyan: #18a8ff;
      --shadow: 0 12px 34px rgba(18, 69, 124, .12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
      color: var(--ink);
      background:
        linear-gradient(180deg, rgba(15, 115, 223, .10), transparent 280px),
        linear-gradient(90deg, rgba(15, 115, 223, .045) 1px, transparent 1px),
        var(--bg);
      background-size: auto, 22px 22px, auto;
    }
    .app {
      width: min(1180px, calc(100vw - 32px));
      margin: 18px auto 38px;
    }
    header {
      min-height: 150px;
      border-radius: 8px;
      padding: 24px;
      color: #fff;
      background:
        linear-gradient(135deg, rgba(9, 47, 99, .98), rgba(15, 115, 223, .95)),
        radial-gradient(circle at 82% 20%, rgba(255,255,255,.25), transparent 32%);
      box-shadow: var(--shadow);
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 22px;
      align-items: center;
    }
    h1 {
      margin: 0 0 8px;
      font-size: clamp(34px, 5vw, 54px);
      line-height: 1.1;
      letter-spacing: 0;
    }
    header p {
      margin: 0;
      color: rgba(255,255,255,.82);
      line-height: 1.7;
      font-size: 16px;
    }
    .badge {
      border: 1px solid rgba(255,255,255,.28);
      background: rgba(255,255,255,.12);
      border-radius: 999px;
      padding: 10px 14px;
      font-weight: 800;
      white-space: nowrap;
    }
    .stepbar {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin-top: 14px;
    }
    .step {
      background: rgba(255,255,255,.88);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 8px 20px rgba(18, 69, 124, .08);
      color: var(--blue-dark);
      padding: 12px 14px;
      font-weight: 800;
    }
    .step span {
      display: inline-grid;
      place-items: center;
      width: 24px;
      height: 24px;
      margin-right: 8px;
      border-radius: 999px;
      background: var(--blue);
      color: #fff;
      font-size: 13px;
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      margin-top: 18px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 20px;
      position: relative;
      overflow: hidden;
    }
    .card::before {
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 3px;
      background: linear-gradient(90deg, var(--blue), var(--cyan));
    }
    .wide { grid-column: 1 / -1; }
    h2 {
      margin: 0 0 16px;
      font-size: 20px;
      letter-spacing: 0;
      color: var(--blue-dark);
    }
    label {
      display: block;
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 8px;
    }
    input[type="text"] {
      width: 100%;
      height: 46px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 14px;
      color: var(--ink);
      background: #fff;
      font: inherit;
      outline: none;
    }
    input[type="text"]:focus {
      border-color: rgba(15, 115, 223, .75);
      box-shadow: 0 0 0 4px rgba(15, 115, 223, .12);
    }
    .row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    button {
      border: 0;
      border-radius: 8px;
      min-height: 46px;
      padding: 0 18px;
      color: #fff;
      background: var(--blue);
      font: inherit;
      font-weight: 800;
      cursor: pointer;
      transition: transform .12s ease, box-shadow .12s ease, background .12s ease;
    }
    button:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 8px 18px rgba(15, 115, 223, .18); }
    button:active:not(:disabled) { transform: translateY(0); box-shadow: none; }
    button.secondary { background: #e9f1fb; color: var(--blue-dark); }
    button.success { background: var(--green); }
    button.danger { background: var(--red); }
    button:disabled { opacity: .52; cursor: not-allowed; }
    .chips {
      display: grid;
      gap: 8px;
      margin: 10px 0 14px;
    }
    .chip {
      background: #eff6ff;
      border: 1px solid var(--line);
      color: var(--blue-dark);
      border-radius: 8px;
      padding: 10px 12px;
      min-height: 42px;
      font-family: Consolas, "Microsoft YaHei", monospace;
      overflow-wrap: anywhere;
    }
    .drop {
      min-height: 132px;
      border: 2px dashed #abc5df;
      border-radius: 8px;
      background:
        linear-gradient(180deg, rgba(255,255,255,.72), rgba(247,250,255,.92)),
        var(--soft);
      display: grid;
      place-items: center;
      text-align: center;
      padding: 20px;
      transition: .16s ease;
    }
    .drop.active { border-color: var(--blue); background: #eaf4ff; }
    .drop strong { display: block; font-size: 20px; margin-bottom: 8px; }
    .drop span { color: var(--muted); line-height: 1.7; }
    .file-input { display: none; }
    .checks {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 14px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 48px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--soft);
      padding: 12px;
      color: var(--ink);
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin: 16px 0;
    }
    .stat {
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-width: 0;
    }
    .stat span { display: block; color: var(--muted); font-size: 13px; margin-bottom: 6px; }
    .stat strong { color: var(--blue-dark); font-size: 18px; }
    progress {
      width: 100%;
      height: 14px;
      appearance: none;
    }
    progress::-webkit-progress-bar { background: #dfe9f5; border-radius: 999px; }
    progress::-webkit-progress-value {
      background: linear-gradient(90deg, #0f73df, #18a8ff);
      border-radius: 999px;
    }
    .status {
      min-height: 44px;
      margin: 0 0 12px;
      padding: 11px 12px;
      border-radius: 8px;
      background: #eef6ff;
      border: 1px solid var(--line);
      color: var(--blue-dark);
      font-weight: 900;
    }
    .hint {
      color: var(--muted);
      line-height: 1.7;
      margin: 10px 0 0;
    }
    pre {
      white-space: pre-wrap;
      min-height: 168px;
      max-height: 280px;
      overflow: auto;
      margin: 0;
      padding: 14px;
      background: #08192f;
      color: #d7eaff;
      border-radius: 8px;
      line-height: 1.65;
    }
    @media (max-width: 840px) {
      .app { width: min(100vw - 18px, 720px); margin-top: 10px; }
      header { grid-template-columns: 1fr; min-height: 156px; padding: 22px; }
      .badge { width: fit-content; }
      .grid { grid-template-columns: 1fr; }
      .checks, .stats { grid-template-columns: 1fr; }
      button { width: 100%; }
    }
  </style>
</head>
<body>
  <main class="app">
    <header>
      <section>
        <h1>北洋闪传</h1>
        <p>电脑端网页控制台 · 操作步骤与手机端一致 · TCP 直连 · SHA-256 校验</p>
      </section>
      <div class="badge">TCP: <span id="tcpPort">50022</span></div>
    </header>

    <section class="stepbar" aria-label="传输步骤">
      <div class="step"><span>1</span>接收方监听</div>
      <div class="step"><span>2</span>发送方输 IP</div>
      <div class="step"><span>3</span>选择文件</div>
      <div class="step"><span>4</span>立即发送</div>
    </section>

    <section class="grid">
      <div class="card">
        <h2>1. 连接设备</h2>
        <label>我的 IP</label>
        <div id="ipChips" class="chips"></div>
        <label for="peerIp">对方 IP</label>
        <input id="peerIp" type="text" placeholder="接收方显示的 IP，例如 192.168.1.8">
        <div class="row" style="margin-top:12px">
          <button id="receiveBtn" class="success">开始接收</button>
          <button id="refreshBtn" class="secondary">刷新 IP</button>
        </div>
        <p class="hint">接收方先点“开始接收”，发送方输入这里显示的 IP 后发送；对方 IP 会自动记住。</p>
      </div>

      <div class="card">
        <h2>2. 文件与保存</h2>
        <label for="saveDir">保存位置</label>
        <div class="row">
          <input id="saveDir" type="text" autocomplete="off">
          <button id="saveBtn" class="secondary">保存位置</button>
        </div>
        <input id="fileInput" class="file-input" type="file">
        <div id="drop" class="drop" style="margin-top:14px">
          <div>
            <strong id="fileTitle">选择文件</strong>
            <span id="fileMeta">电脑发送时，先点击这里选择要发送的文件。</span>
          </div>
        </div>
        <div class="checks">
          <label class="check"><input id="zipBefore" type="checkbox"> 发送前压缩 ZIP</label>
          <label class="check"><input id="autoUnzip" type="checkbox" checked> 接收后自动解压</label>
        </div>
      </div>

      <div class="card wide">
        <h2>3. 传输监控</h2>
        <div class="status" id="status">等待操作</div>
        <progress id="progress" value="0" max="100"></progress>
        <div class="stats">
          <div class="stat"><span>进度</span><strong id="percent">0%</strong></div>
          <div class="stat"><span>速度</span><strong id="speed">--</strong></div>
          <div class="stat"><span>剩余</span><strong id="eta">--</strong></div>
          <div class="stat"><span>当前文件</span><strong id="currentFile">--</strong></div>
        </div>
        <div class="row">
          <button id="sendBtn" disabled>立即发送</button>
          <button id="cancelBtn" class="danger">取消传输</button>
        </div>
      </div>

      <div class="card wide">
        <h2>运行记录</h2>
        <pre id="log">网页端已就绪。</pre>
      </div>
    </section>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    const drop = $("drop");
    const fileInput = $("fileInput");
    let selectedFile = null;
    let stagedKey = "";
    let busy = false;
    const PEER_IP_KEY = "beiyang_flash_peer_ip";

    function fmt(bytes) {
      if (!Number.isFinite(bytes)) return "--";
      const units = ["B", "KB", "MB", "GB", "TB"];
      let value = bytes;
      let index = 0;
      while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index++;
      }
      return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
    }

    async function api(path, options = {}) {
      const response = await fetch(path, options);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `请求失败：${response.status}`);
      return data;
    }

    function showLog(lines) {
      $("log").textContent = lines && lines.length ? lines.join("\n") : "网页端已就绪。";
      $("log").scrollTop = $("log").scrollHeight;
    }

    function applyStatus(data) {
      busy = !!data.busy;
      $("status").textContent = data.status || "等待操作";
      $("progress").value = data.progress || 0;
      $("percent").textContent = `${data.progress || 0}%`;
      $("speed").textContent = data.speed || "--";
      $("eta").textContent = data.eta || "--";
      $("currentFile").textContent = data.file_name || "--";
      showLog(data.events || []);
      $("receiveBtn").disabled = busy;
      $("sendBtn").disabled = busy || !selectedFile;
      $("cancelBtn").disabled = !busy;
    }

    async function loadInfo() {
      const info = await api("/api/info");
      $("tcpPort").textContent = info.tcp_port;
      $("saveDir").value = info.save_dir;
      $("peerIp").value = localStorage.getItem(PEER_IP_KEY) || $("peerIp").value;
      $("ipChips").innerHTML = info.ips.map(ip => `<div class="chip">${ip}</div>`).join("");
      applyStatus(info.state);
    }

    async function refreshStatus() {
      try {
        applyStatus(await api("/api/status"));
      } catch (_) {}
    }

    $("refreshBtn").addEventListener("click", loadInfo);
    $("peerIp").addEventListener("input", () => {
      const value = $("peerIp").value.trim();
      if (value) localStorage.setItem(PEER_IP_KEY, value);
    });
    $("saveBtn").addEventListener("click", async () => {
      try {
        const data = await api("/api/config", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({save_dir: $("saveDir").value})
        });
        $("saveDir").value = data.save_dir;
        applyStatus(data.state);
      } catch (error) {
        alert(error.message);
      }
    });

    function chooseFile(file) {
      selectedFile = file;
      stagedKey = "";
      $("fileTitle").textContent = file.name;
      $("fileMeta").textContent = `${fmt(file.size)} · ${file.type || "未知类型"}`;
      $("sendBtn").disabled = busy || !selectedFile;
    }

    drop.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      if (fileInput.files[0]) chooseFile(fileInput.files[0]);
    });
    ["dragenter", "dragover"].forEach(name => drop.addEventListener(name, event => {
      event.preventDefault();
      drop.classList.add("active");
    }));
    ["dragleave", "drop"].forEach(name => drop.addEventListener(name, event => {
      event.preventDefault();
      drop.classList.remove("active");
    }));
    drop.addEventListener("drop", event => {
      const file = event.dataTransfer.files[0];
      if (file) chooseFile(file);
    });

    async function stageSelectedFile() {
      if (!selectedFile) throw new Error("请先选择要发送的文件");
      const key = `${selectedFile.name}|${selectedFile.size}|${selectedFile.lastModified}`;
      if (stagedKey === key) return;
      $("status").textContent = "正在读取电脑文件";
      const url = `/api/file/stage?filename=${encodeURIComponent(selectedFile.name)}&size=${selectedFile.size}`;
      const data = await api(url, {
        method: "POST",
        headers: {"Content-Type": "application/octet-stream"},
        body: selectedFile
      });
      stagedKey = key;
      applyStatus(data.state);
    }

    $("receiveBtn").addEventListener("click", async () => {
      try {
        const data = await api("/api/receive/start", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({auto_unzip: $("autoUnzip").checked})
        });
        applyStatus(data.state);
      } catch (error) {
        alert(error.message);
      }
    });

    $("sendBtn").addEventListener("click", async () => {
      try {
        await stageSelectedFile();
        const peerIp = $("peerIp").value.trim();
        if (!peerIp) throw new Error("请先输入对方 IP");
        localStorage.setItem(PEER_IP_KEY, peerIp);
        const data = await api("/api/send/start", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({target_ip: peerIp, compress: $("zipBefore").checked})
        });
        applyStatus(data.state);
      } catch (error) {
        alert(error.message);
      }
    });

    $("cancelBtn").addEventListener("click", async () => {
      try {
        const data = await api("/api/cancel", {method: "POST"});
        applyStatus(data.state);
      } catch (error) {
        alert(error.message);
      }
    });

    loadInfo().catch(error => alert(error.message));
    setInterval(refreshStatus, 1000);
  </script>
</body>
</html>
"""


class DesktopWebState:
    def __init__(self):
        self.lock = threading.Lock()
        self.transfer_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.server_sock = None
        self.active_sock = None
        self.temp_zip = None
        self.save_dir = None
        for candidate in (
            Path.home() / "Downloads" / "北洋闪传",
            Path.cwd() / "received" / "北洋闪传",
            Path(tempfile.gettempdir()) / "北洋闪传",
        ):
            try:
                self.save_dir = test_writable_dir(candidate)
                break
            except OSError:
                continue
        if self.save_dir is None:
            raise OSError("没有找到可写的默认保存目录")
        self.selected_file = None
        self.selected_name = ""
        self.selected_size = 0
        self.events = []
        self.busy = False
        self.mode = "idle"
        self.status = "等待操作"
        self.progress = 0
        self.speed = "--"
        self.eta = "--"
        self.file_name = ""
        self.started_at = None
        self.start_offset = 0

    def log(self, message):
        now = time.strftime("%H:%M:%S")
        with self.lock:
            self.events.append(f"[{now}] {message}")
            self.events = self.events[-100:]

    def snapshot(self):
        with self.lock:
            return {
                "save_dir": self.save_dir,
                "selected_name": self.selected_name,
                "selected_size": self.selected_size,
                "busy": self.busy,
                "mode": self.mode,
                "status": self.status,
                "progress": self.progress,
                "speed": self.speed,
                "eta": self.eta,
                "file_name": self.file_name,
                "events": list(self.events),
            }

    def set_status(self, status, mode=None, busy=None, file_name=None):
        with self.lock:
            self.status = status
            if mode is not None:
                self.mode = mode
            if busy is not None:
                self.busy = busy
            if file_name is not None:
                self.file_name = file_name

    def reset_progress(self):
        with self.lock:
            self.progress = 0
            self.speed = "--"
            self.eta = "--"
            self.started_at = time.monotonic()
            self.start_offset = 0

    def begin_meter(self, offset):
        with self.lock:
            self.started_at = time.monotonic()
            self.start_offset = offset

    def update_progress(self, done, total, label):
        pct = int(done * 100 / total) if total else 0
        with self.lock:
            started_at = self.started_at or time.monotonic()
            start_offset = self.start_offset
        elapsed = max(time.monotonic() - started_at, 0.001)
        session_done = max(done - start_offset, 0)
        speed_value = session_done / elapsed
        eta_value = (total - done) / speed_value if speed_value > 1 else float("inf")
        with self.lock:
            self.progress = pct
            self.speed = f"{format_bytes(speed_value)}/s"
            self.eta = format_seconds(eta_value)
            self.status = f"{label} · {format_bytes(done)} / {format_bytes(total)}"

    def set_selected_file(self, path, name, size):
        with self.lock:
            self.selected_file = path
            self.selected_name = name
            self.selected_size = size
            self.file_name = name

    def close_sockets(self):
        for attr in ("active_sock", "server_sock"):
            sock = getattr(self, attr, None)
            try:
                if sock:
                    sock.shutdown(socket.SHUT_RDWR)
                    sock.close()
            except Exception:
                pass
            setattr(self, attr, None)


def parse_target_address(text):
    value = (text or "").strip()
    if not value:
        raise ValueError("请先输入对方 IP")
    value = value.replace("http://", "").replace("https://", "").split("/")[0]
    if ":" in value:
        host, raw_port = value.rsplit(":", 1)
        host = host.strip()
        if not host:
            raise ValueError("对方 IP 不能为空")
        return host, int(raw_port)
    return value, PORT


def make_transfer_zip(file_path):
    source = Path(file_path)
    temp_dir = Path(tempfile.gettempdir())
    target = unique_path(temp_dir, f"{source.stem}_fast_transfer.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(source, arcname=source.name)
    return str(target)


def desktop_receive_worker(state, auto_unzip=True):
    if not state.transfer_lock.acquire(blocking=False):
        state.log("已有传输正在进行。")
        return
    try:
        state.cancel_event.clear()
        state.reset_progress()
        state.set_status(f"监听中 0.0.0.0:{PORT}", mode="receive", busy=True, file_name="")
        state.log(f"正在监听 0.0.0.0:{PORT}，等待发送方连接...")
        state.server_sock = tune_transfer_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        state.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        state.server_sock.bind(("0.0.0.0", PORT))
        state.server_sock.listen(5)
        conn, addr = state.server_sock.accept()
        state.active_sock = tune_transfer_socket(conn)
        conn.settimeout(SOCKET_TIMEOUT)
        state.log(f"已连接发送方：{addr[0]}")

        meta = recv_json(conn)
        conn.settimeout(TRANSFER_TIMEOUT)
        filename = safe_filename(meta["filename"])
        total_size = int(meta["size"])
        digest = meta["sha256"]
        compressed = bool(meta.get("compressed"))
        state.set_status("准备接收文件", file_name=filename)

        save_dir = Path(state.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        final_path = unique_path(save_dir, filename)
        part_path = Path(str(final_path) + ".part")
        state_path = Path(str(final_path) + ".state.json")
        offset = 0
        if part_path.exists() and state_path.exists():
            old = json.loads(state_path.read_text(encoding="utf-8"))
            if old.get("sha256") == digest and old.get("filename") == filename:
                offset = min(part_path.stat().st_size, total_size)

        state_path.write_text(
            json.dumps({"filename": filename, "size": total_size, "sha256": digest}, ensure_ascii=False),
            encoding="utf-8",
        )
        send_json(conn, {"ok": True, "offset": offset})

        bytes_received = offset
        state.begin_meter(offset)
        state.log(f"开始接收：{filename}，从 {format_bytes(offset)} 处继续。")
        with open(part_path, "ab" if offset else "wb") as f:
            while bytes_received < total_size:
                if state.cancel_event.is_set():
                    raise InterruptedError("用户取消")
                chunk = conn.recv(min(CHUNK_SIZE, total_size - bytes_received))
                if not chunk:
                    raise ConnectionError("连接中断，已保留断点文件")
                f.write(chunk)
                bytes_received += len(chunk)
                state.update_progress(bytes_received, total_size, f"接收 {filename}")
            f.flush()
            os.fsync(f.fileno())

        state.set_status("正在校验 SHA-256")
        actual_digest, _ = sha256_file(part_path, state.cancel_event)
        if actual_digest != digest:
            raise ValueError("SHA-256 校验失败，请重新传输")
        part_path.replace(final_path)
        state_path.unlink(missing_ok=True)
        send_json(conn, {"done": True, "sha256": actual_digest})
        state.update_progress(total_size, total_size, f"完成 {filename}")
        state.set_status("接收完成，校验通过", mode="idle", busy=False)
        state.log(f"接收完成并已保存：{final_path}")

        if compressed and auto_unzip and zipfile.is_zipfile(final_path):
            extract_dir = unique_dir(final_path.with_suffix(""))
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(final_path, "r") as zf:
                zf.extractall(extract_dir)
            state.log(f"已自动解压到：{extract_dir}")
    except InterruptedError:
        state.set_status("接收已取消，断点保留", mode="idle", busy=False)
        state.log("接收已取消，断点文件已保留。")
    except Exception as exc:
        state.set_status("接收失败", mode="idle", busy=False)
        if isinstance(exc, socket.timeout):
            state.log(f"接收失败：{int(TRANSFER_TIMEOUT)} 秒内没有收到数据，断点文件已保留，可重新发送继续。")
        else:
            state.log(f"接收失败：{exc}")
    finally:
        state.close_sockets()
        with state.lock:
            state.busy = False
        state.transfer_lock.release()


def desktop_send_worker(state, target_text, compress=False):
    if not state.transfer_lock.acquire(blocking=False):
        state.log("已有传输正在进行。")
        return
    send_path = None
    temp_zip = None
    try:
        with state.lock:
            selected_file = state.selected_file
        if not selected_file or not os.path.isfile(selected_file):
            raise ValueError("请先选择要发送的文件")
        host, target_port = parse_target_address(target_text)
        state.cancel_event.clear()
        state.reset_progress()
        state.set_status("准备发送", mode="send", busy=True, file_name=os.path.basename(selected_file))
        send_path = selected_file
        if compress:
            state.set_status("正在压缩 ZIP")
            temp_zip = make_transfer_zip(selected_file)
            send_path = temp_zip
            state.log(f"已生成压缩文件：{send_path}")

        state.set_status("正在生成 SHA-256")
        digest, total_size = sha256_file(send_path, state.cancel_event)
        filename = os.path.basename(send_path)
        state.set_status(f"连接 {host}:{target_port}", file_name=filename)
        state.log(f"正在连接 {host}:{target_port} ...")
        sock = connect_tcp_with_retries(host, target_port, state.log)
        state.active_sock = sock
        send_json(
            sock,
            {
                "op": "offer",
                "filename": filename,
                "size": total_size,
                "sha256": digest,
                "compressed": compress,
                "chunk_size": CHUNK_SIZE,
            },
        )
        reply = recv_json(sock)
        if not reply.get("ok"):
            raise ConnectionError("接收方拒绝了文件")
        offset = int(reply.get("offset", 0))
        sock.settimeout(TRANSFER_TIMEOUT)
        state.begin_meter(offset)
        state.log(f"开始发送：{filename}，从 {format_bytes(offset)} 处继续。")
        bytes_sent = offset
        with open(send_path, "rb") as f:
            f.seek(offset)
            while bytes_sent < total_size:
                if state.cancel_event.is_set():
                    raise InterruptedError("用户取消")
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sock.sendall(chunk)
                bytes_sent += len(chunk)
                state.update_progress(bytes_sent, total_size, f"发送 {filename}")

        sock.settimeout(VERIFY_TIMEOUT)
        final_reply = recv_json(sock)
        if final_reply.get("sha256") != digest:
            raise ValueError("未收到接收方校验通过确认")
        state.update_progress(total_size, total_size, f"完成 {filename}")
        state.set_status("发送完成，校验通过", mode="idle", busy=False)
        state.log("发送完成，接收方校验通过。")
    except InterruptedError:
        state.set_status("发送已取消", mode="idle", busy=False)
        state.log("发送已取消。")
    except Exception as exc:
        state.set_status("发送失败", mode="idle", busy=False)
        if isinstance(exc, socket.timeout):
            state.log("发送失败：网络长时间无响应。可重新发送，程序会尝试断点续传。")
        else:
            state.log(f"发送失败：{exc}")
        state.log(
            "连接提示：若热点可传但校园网不能传，通常是校园网客户端隔离或阻止入站 TCP。"
            "请换手机热点或同一不隔离局域网。"
        )
    finally:
        state.close_sockets()
        if temp_zip:
            try:
                os.remove(temp_zip)
            except OSError:
                pass
        with state.lock:
            state.busy = False
        state.transfer_lock.release()


def create_desktop_web_handler(state, web_port):
    class DesktopWebHandler(BaseHTTPRequestHandler):
        server_version = "BeiyangFlashDesktop/1.0"

        def log_message(self, *_):
            return

        def send_json_response(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_json_body(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                body = DESKTOP_WEB_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/info":
                ips = get_local_ips()
                self.send_json_response(
                    {
                        "app": "北洋闪传",
                        "web_port": web_port,
                        "tcp_port": PORT,
                        "ips": ips,
                        "urls": [f"http://{ip}:{web_port}" for ip in ips],
                        "save_dir": state.save_dir,
                        "state": state.snapshot(),
                    }
                )
                return
            if parsed.path == "/api/status":
                self.send_json_response(state.snapshot())
                return
            self.send_json_response({"error": "页面不存在"}, 404)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path == "/api/config":
                    data = self.read_json_body()
                    selected = (data.get("save_dir") or "").strip()
                    if not selected:
                        raise ValueError("保存目录不能为空")
                    save_dir = test_writable_dir(selected)
                    with state.lock:
                        state.save_dir = save_dir
                    state.log(f"保存位置已设置为：{save_dir}")
                    self.send_json_response({"ok": True, "save_dir": save_dir, "state": state.snapshot()})
                    return

                if parsed.path == "/api/file/stage":
                    params = urllib.parse.parse_qs(parsed.query)
                    filename = safe_filename((params.get("filename") or ["selected_file"])[0])
                    expected_size = int((params.get("size") or ["0"])[0])
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    staging_dir = Path(tempfile.gettempdir()) / "beiyang_flash_staged"
                    staging_dir.mkdir(parents=True, exist_ok=True)
                    target = unique_path(staging_dir, filename)
                    received = 0
                    state.set_status("正在读取电脑文件", mode="stage", busy=True, file_name=filename)
                    with open(target, "wb") as f:
                        while received < length:
                            chunk = self.rfile.read(min(WEB_CHUNK_SIZE, length - received))
                            if not chunk:
                                break
                            f.write(chunk)
                            received += len(chunk)
                    if expected_size and received != expected_size:
                        raise ValueError("读取文件不完整，请重新选择")
                    state.set_selected_file(str(target), filename, received)
                    state.set_status("文件已选择，等待发送", mode="idle", busy=False, file_name=filename)
                    state.log(f"已选择文件：{filename} ({format_bytes(received)})")
                    self.send_json_response({"ok": True, "file": filename, "size": received, "state": state.snapshot()})
                    return

                if parsed.path == "/api/receive/start":
                    data = self.read_json_body()
                    threading.Thread(
                        target=desktop_receive_worker,
                        args=(state, bool(data.get("auto_unzip", True))),
                        daemon=True,
                    ).start()
                    time.sleep(0.05)
                    self.send_json_response({"ok": True, "state": state.snapshot()})
                    return

                if parsed.path == "/api/send/start":
                    data = self.read_json_body()
                    threading.Thread(
                        target=desktop_send_worker,
                        args=(state, data.get("target_ip", ""), bool(data.get("compress"))),
                        daemon=True,
                    ).start()
                    time.sleep(0.05)
                    self.send_json_response({"ok": True, "state": state.snapshot()})
                    return

                if parsed.path == "/api/cancel":
                    state.cancel_event.set()
                    state.close_sockets()
                    state.set_status("已取消，断点保留", mode="idle", busy=False)
                    state.log("已发出取消请求。")
                    self.send_json_response({"ok": True, "state": state.snapshot()})
                    return

                self.send_json_response({"error": "接口不存在"}, 404)
            except Exception as exc:
                with state.lock:
                    state.busy = False
                state.log(f"请求失败：{exc}")
                self.send_json_response({"error": str(exc), "state": state.snapshot()}, 400)

    return DesktopWebHandler


def run_web_app(open_browser=True):
    state = DesktopWebState()
    last_error = None
    for web_port in [WEB_CONTROL_PORT, 8000, 8001, 8010, 8080]:
        try:
            handler = create_desktop_web_handler(state, web_port)
            server = ThreadingHTTPServer(("0.0.0.0", web_port), handler)
            break
        except OSError as exc:
            last_error = exc
    else:
        raise OSError(f"无法启动网页服务：{last_error}")

    urls = [f"http://{ip}:{server.server_port}" for ip in get_local_ips()]
    local_url = f"http://127.0.0.1:{server.server_port}"
    print("北洋闪传电脑端已启动")
    print(f"网页控制台：{local_url}")
    print("同一局域网设备可打开：")
    for url in urls:
        print(f"  {url}")
    print(f"文件传输 TCP 端口：{PORT}")
    print(f"保存目录：{state.save_dir}")
    print("按 Ctrl+C 停止服务")
    if open_browser:
        try:
            webbrowser.open(local_url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止北洋闪传电脑端")
    finally:
        state.close_sockets()
        server.server_close()


if __name__ == "__main__":
    if platform == "android" or "--kivy" in sys.argv:
        FileTransferApp().run()
    else:
        run_web_app(open_browser="--no-browser" not in sys.argv)
