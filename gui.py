#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

sys.dont_write_bytecode = True

import unpacker


WINDOW_TITLE = "孙笑川258专用解压工具"
WINDOW_SIZE = "520x360"
DEFAULT_PASSWORD = "孙笑川258"
STARTUP_NOTES = (
    "孙笑川258专用解压工具",
    r"【运行说明】 WINRAR必须安装在C:\Program Files\WinRAR中",
    "【输出】输出目录为空时默认输出到原文件夹",
    "【注意】看不懂的功能不要点",
)


class QueueLogger:
    def __init__(self, out_queue: queue.Queue[tuple[str, str]]) -> None:
        self.out_queue = out_queue

    def log(self, message: str) -> None:
        self.out_queue.put(("log", str(message)))


def parse_password_pool(raw: str) -> list[str]:
    """
    GUI 密码池解析。
    支持英文逗号 , 和中文逗号 ， 分割。
    自动去掉首尾空格。
    自动去重。
    保持输入顺序。
    """
    if not raw:
        return []

    normalized = raw.replace("，", ",")
    result: list[str] = []

    for item in normalized.split(","):
        password = item.strip()
        if password and password not in result:
            result.append(password)

    return result


def hide_folder_if_exists(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        if path.exists() and path.is_dir():
            FILE_ATTRIBUTE_HIDDEN = 0x02
            ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


class UnpackerGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(500, 330)

        self.selected_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.password = tk.StringVar(value=DEFAULT_PASSWORD)
        self.status = tk.StringVar(value="就绪")
        self.use_ramdisk = tk.BooleanVar(value=False)

        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.control: unpacker.TaskControl | None = None
        self.paused = False
        self.closing = False

        self._build_ui()
        self._set_running_state(False)
        self._append_startup_notes()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_messages)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        path_frame = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        path_frame.grid(row=0, column=0, sticky="ew")
        path_frame.columnconfigure(1, weight=1)

        ttk.Label(path_frame, text="输入", width=4).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(path_frame, textvariable=self.selected_path, width=30).grid(row=0, column=1, sticky="ew")
        ttk.Button(path_frame, text="文件", width=5, command=self._choose_file).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(path_frame, text="文件夹", width=6, command=self._choose_folder).grid(row=0, column=3, padx=(6, 0))

        output_frame = ttk.Frame(self.root, padding=(10, 0, 10, 4))
        output_frame.grid(row=1, column=0, sticky="ew")
        output_frame.columnconfigure(1, weight=1)

        ttk.Label(output_frame, text="输出", width=4).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(output_frame, textvariable=self.output_path, width=30).grid(row=0, column=1, sticky="ew")
        ttk.Button(output_frame, text="选择", width=5, command=self._choose_output_folder).grid(row=0, column=2, padx=(6, 0))

        control_frame = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        control_frame.grid(row=2, column=0, sticky="ew")
        control_frame.columnconfigure(1, weight=1)

        ttk.Label(control_frame, text="密码", width=4).grid(row=0, column=0, sticky="w", padx=(0, 6))

        # 明文显示密码，不使用 show="*"
        password_entry = ttk.Entry(control_frame, textvariable=self.password, width=24)
        password_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        ttk.Checkbutton(
            control_frame,
            text="内存盘",
            variable=self.use_ramdisk,
        ).grid(row=0, column=2, sticky="w", padx=(0, 10))

        self.start_button = ttk.Button(control_frame, text="开始", width=5, command=self._start)
        self.pause_button = ttk.Button(control_frame, text="暂停", width=5, command=self._toggle_pause)
        self.stop_button = ttk.Button(control_frame, text="停止", width=5, command=self._stop)

        self.start_button.grid(row=0, column=3, padx=(0, 6))
        self.pause_button.grid(row=0, column=4, padx=(0, 6))
        self.stop_button.grid(row=0, column=5, padx=(0, 6))
        ttk.Label(control_frame, textvariable=self.status).grid(row=0, column=6, sticky="e")

        log_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = ScrolledText(log_frame, wrap="word", height=9)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")

    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(title="选择压缩文件")
        if path:
            self.selected_path.set(path)

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(title="选择文件夹")
        if path:
            self.selected_path.set(path)

    def _choose_output_folder(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_path.set(path)

    def _append_startup_notes(self) -> None:
        for note in STARTUP_NOTES:
            self._append_log(note)

    def _start(self) -> None:
        raw_path = self.selected_path.get().strip().strip('"')
        if not raw_path:
            messagebox.showwarning(WINDOW_TITLE, "请先选择文件或文件夹。")
            return

        path = Path(raw_path)
        if not path.exists():
            messagebox.showerror(WINDOW_TITLE, "选择的路径不存在。")
            return

        raw_output = self.output_path.get().strip().strip('"')
        output_override = Path(raw_output) if raw_output else None

        if output_override is not None:
            try:
                output_override.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                messagebox.showerror(
                    WINDOW_TITLE,
                    f"无法创建输出目录：\n{output_override}\n\n{exc}",
                )
                return

        if not self._prepare_ramdisk():
            return

        control = unpacker.TaskControl()
        self.control = control
        self.paused = False
        self._set_running_state(True)
        self._append_log("[开始] 任务已启动")
        password_count = len(parse_password_pool(self.password.get()))
        self._append_log(f"[密码池] 已设置 {password_count} 个候选密码")
        if output_override is None:
            self._append_log("[输出目录] 未指定，使用源文件所在目录")
        else:
            self._append_log(f"[输出目录] {output_override}")

        worker = threading.Thread(
            target=self._run_worker,
            args=(path, self.password.get(), output_override, control),
            daemon=True,
        )
        self.worker = worker
        worker.start()

    def _prepare_ramdisk(self) -> bool:
        if not self.use_ramdisk.get():
            unpacker.AUTO_RAMDISK = False
            unpacker.TEMP_MODE = "same_volume"
            unpacker.CUSTOM_TEMP_DIR = ""
            unpacker.CUSTOM_TEMP_IS_RAMDISK = False
            unpacker.AUTO_RAMDISK_SELECTED_DIR = ""
            self._append_log("[RAM Disk] 已关闭，跳过内存盘检测，使用普通临时目录")
            return True

        unpacker.AUTO_RAMDISK = True

        if unpacker.has_manual_temp_args():
            return True

        ok, reason = unpacker.apply_auto_ramdisk_config(True)
        if ok:
            self._append_log(f"[RAM Disk] {reason}")
            return True

        return messagebox.askyesno(
            WINDOW_TITLE,
            "未能自动创建或启用内存盘。\n\n"
            f"原因：{reason}\n\n"
            "是否继续运行？继续后会使用普通临时目录，不能获得完整的内存盘保护。",
        )

    def _run_worker(
        self,
        path: Path,
        password_text: str,
        output_override: Path | None,
        control: unpacker.TaskControl,
    ) -> None:
        logger = QueueLogger(self.messages)
        passwords = parse_password_pool(password_text)
        engine: unpacker.NestedUnpacker | None = None

        try:
            unpacker.install_exit_cleanup_handlers()
            engine = unpacker.NestedUnpacker(
                passwords=passwords,
                quiet=True,
                logger=logger,
                control=control,
                output_override=output_override,
            )
            unpacker._ACTIVE_UNPACKER = engine
            engine.run([path])
            self.messages.put(("done", "[完成] 解压任务结束"))

        except unpacker.UserStoppedError:
            self.messages.put(("stopped", "[已停止] 用户停止任务，临时文件已清理"))

        except SystemExit as exc:
            code = getattr(exc, "code", 1)
            self.messages.put(("error", f"[终止] 任务已结束，代码：{code}"))

        except Exception as exc:
            self.messages.put(("error", f"[错误] {exc}"))

        finally:
            if engine is not None:
                try:
                    engine.close()
                except Exception:
                    pass

            try:
                unpacker.release_memory_now()
            except Exception:
                pass

            self.messages.put(("idle", ""))

    def _toggle_pause(self) -> None:
        if self.control is None:
            return

        if self.paused:
            self.control.resume()
            self.paused = False
            self.pause_button.configure(text="暂停")
            self.status.set("运行中")
            self._append_log("[继续] 任务已恢复")
        else:
            self.control.pause()
            self.paused = True
            self.pause_button.configure(text="继续")
            self.status.set("已暂停")
            self._append_log("[暂停] 任务将在下一个检查点暂停")

    def _stop(self) -> None:
        if self.control is None:
            return

        self.control.stop()
        self.status.set("正在停止")
        self._append_log("[停止] 已请求停止任务")

    def _set_running_state(self, running: bool) -> None:
        if running:
            self.start_button.configure(state="disabled")
            self.pause_button.configure(state="normal", text="暂停")
            self.stop_button.configure(state="normal")
            self.status.set("运行中")
        else:
            self.start_button.configure(state="normal")
            self.pause_button.configure(state="disabled", text="暂停")
            self.stop_button.configure(state="disabled")
            self.status.set("就绪")
            self.control = None
            self.worker = None
            self.paused = False

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, message = self.messages.get_nowait()

                if message:
                    self._append_log(message)

                if kind in {"done", "stopped", "error"}:
                    if kind == "error":
                        messagebox.showerror(WINDOW_TITLE, "任务执行出错，请查看窗口日志。")
                    self._set_running_state(False)

                elif kind == "idle":
                    self._set_running_state(False)
                    if self.closing:
                        self.root.destroy()
                        return

        except queue.Empty:
            pass

        self.root.after(100, self._drain_messages)

    def _on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive() and self.control is not None:
            if not messagebox.askyesno(WINDOW_TITLE, "任务正在运行，是否停止并关闭？"):
                return

            self.closing = True
            self.control.stop()
            self.status.set("正在停止")
            self._append_log("[关闭] 正在停止任务并清理临时资源")
            return

        try:
            unpacker.release_memory_now()
        except Exception:
            pass

        self.root.destroy()


def main() -> None:
    hide_folder_if_exists(Path.cwd() / ".runtime_cache")
    hide_folder_if_exists(Path.cwd() / "cache")

    root = tk.Tk()
    UnpackerGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
