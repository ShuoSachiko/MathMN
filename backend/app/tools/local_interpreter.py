"""本地代码解释器模块，通过本地 Jupyter 内核执行 Python 代码。"""

import asyncio
import os

import jupyter_client

from app.config.setting import settings
from app.services.redis_manager import redis_manager
from app.schemas.response import (
    OutputItem,
    ResultModel,
    StdErrModel,
    SystemMessage,
)
from app.tools.base_interpreter import BaseCodeInterpreter
from app.tools.matplotlib_setup import build_matplotlib_init_code
from app.tools.notebook_serializer import NotebookSerializer
from app.utils.log_util import logger


class LocalCodeInterpreter(BaseCodeInterpreter):
    """基于本地 Jupyter 内核的代码解释器。"""
    def __init__(
        self,
        task_id: str,
        work_dir: str,
        notebook_serializer: NotebookSerializer,
    ):
        super().__init__(task_id, work_dir, notebook_serializer)
        self.km, self.kc = None, None
        self.interrupt_signal = False

    async def initialize(self):
        """初始化本地 Jupyter 内核并执行 matplotlib 字体加载。

        本地内核无需上传文件，直接启动内核并切换工作目录即可。
        """
        logger.info("初始化本地内核")
        # 设置 UTF-8 编码环境，避免 Windows 中文环境下 GBK 编码导致的乱码问题
        kernel_env = os.environ.copy()
        kernel_env["PYTHONIOENCODING"] = "utf-8"
        kernel_env["PYTHONUTF8"] = "1"
        # start_new_kernel 会启动子进程并等待内核就绪，属于阻塞调用，
        # 放到线程池执行以免初始化阶段阻塞事件循环
        self.km, self.kc = await asyncio.to_thread(
            jupyter_client.manager.start_new_kernel,
            kernel_name="python3",
            env=kernel_env,
        )
        font_msg, font_type = await self._pre_execute_code()
        if font_msg:
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content=font_msg, type=font_type),
            )

    async def _pre_execute_code(self) -> tuple[str | None, str]:
        """执行 matplotlib 初始化，并解析字体加载结果供前端展示。

        Returns:
            (消息文案, SystemMessage.type)；无可用信息时文案为 None。
        """
        init_code = build_matplotlib_init_code(self.work_dir)
        # 初始化代码同样会阻塞在 IOPub 消息循环上，必须走线程池执行
        execution = await asyncio.to_thread(self.execute_code_, init_code)
        stdout = "\n".join(text for mark, text in execution if mark == "stdout")
        for line in stdout.splitlines():
            line = line.strip()
            if "中文字体已加载" in line:
                # 去掉日志前缀，前端只展示关键结论
                content = line.removeprefix("[matplotlib_setup] ").strip()
                return content, "success"
            if "未找到中文字体" in line:
                content = line.removeprefix("[matplotlib_setup] ").strip()
                return content, "warning"
        return None, "info"

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        """执行一段 Python 代码并返回执行结果。

        Args:
            code: 待执行的 Python 代码。

        Returns:
            (输出文本, 是否出错, 错误信息) 三元组；执行超时时会中断内核并以错误三元组返回，
            不向调用方抛异常。
        """
        logger.info(f"执行代码: {code}")
        #  添加代码到notebook
        self.notebook_serializer.add_code_cell_to_notebook(code)

        text_to_gpt: list[str] = []
        content_to_display: list[OutputItem] | None = []
        error_occurred: bool = False
        error_message: str = ""

        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="开始执行代码"),
        )
        # 执行 Python 代码
        logger.info("开始在本地执行代码...")
        try:
            # jupyter 客户端的 IOPub 消息循环是同步阻塞调用，必须放到线程池执行，
            # 否则会卡死 asyncio 事件循环，导致 WebSocket 心跳、取消信号和并发任务全部失效。
            # 外层再用 wait_for 限制最长时间，防止死循环或超长计算让任务永久挂起。
            execution = await asyncio.wait_for(
                asyncio.to_thread(self.execute_code_, code),
                timeout=settings.INTERPRETER_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # 超时后必须中断内核，否则后台线程仍在等待消息，僵尸内核还会影响后续执行
            self._interrupt_kernel()
            error_message = (
                f"代码执行超时（超过 {settings.INTERPRETER_TIMEOUT_SECONDS} 秒），"
                "已中断内核"
            )
            logger.error(error_message)
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content=error_message, type="error"),
            )
            await self._push_to_websocket([StdErrModel(msg=error_message)])
            return (error_message, True, error_message)
        except asyncio.CancelledError:
            # 任务被取消时同样需要中断内核，防止内核继续执行后续代码
            self._interrupt_kernel()
            logger.warning("代码执行被取消，已中断内核")
            raise
        logger.info("代码执行完成，开始处理结果...")

        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="代码执行完成"),
        )

        for mark, out_str in execution:
            if mark in ("stdout", "execute_result_text", "display_text"):
                text_to_gpt.append(self._truncate_text(f"[{mark}]\n{out_str}"))
                #  添加text到notebook
                content_to_display.append(
                    ResultModel(res_type="result", format="text", msg=out_str)
                )
                self.notebook_serializer.add_code_cell_output_to_notebook(out_str)

            elif mark in (
                "execute_result_png",
                "execute_result_jpeg",
                "display_png",
                "display_jpeg",
            ):
                # TODO: 视觉模型解释图像
                text_to_gpt.append(f"[{mark} 图片已生成，内容为 base64，未展示]")

                #  添加image到notebook
                if "png" in mark:
                    self.notebook_serializer.add_image_to_notebook(out_str, "image/png")
                    content_to_display.append(
                        ResultModel(res_type="result", format="png", msg=out_str)
                    )
                else:
                    self.notebook_serializer.add_image_to_notebook(
                        out_str, "image/jpeg"
                    )
                    content_to_display.append(
                        ResultModel(res_type="result", format="jpeg", msg=out_str)
                    )

            elif mark == "error":
                error_occurred = True
                error_message = self.delete_color_control_char(out_str)
                error_message = self._truncate_text(error_message)
                logger.error(f"执行错误: {error_message}")
                text_to_gpt.append(error_message)
                #  添加error到notebook
                self.notebook_serializer.add_code_cell_error_to_notebook(out_str)
                content_to_display.append(StdErrModel(msg=out_str))

        logger.info(f"text_to_gpt: {text_to_gpt}")
        combined_text = "\n".join(text_to_gpt)

        # 成功路径把本次 stdout 摘要写入 section 内容缓存，供 Writer 通过
        # get_code_output 读取，修复解释器输出从不进入论文的数值断链问题
        if not error_occurred:
            self.add_content(self.current_section or "default", combined_text)

        await self._push_to_websocket(content_to_display)

        return (
            combined_text,
            error_occurred,
            error_message,
        )

    def execute_code_(self, code) -> list[tuple[str, str]]:
        assert self.kc is not None
        assert self.km is not None
        self.kc.execute(code)
        logger.info(f"执行代码: {code}")
        # Get the output of the code
        msg_list = []
        while True:
            try:
                iopub_msg = self.kc.get_iopub_msg(timeout=1)
                msg_list.append(iopub_msg)
                if (
                    iopub_msg["msg_type"] == "status"
                    and iopub_msg["content"].get("execution_state") == "idle"
                ):
                    break
            except Exception:
                if self.interrupt_signal:
                    self.km.interrupt_kernel()
                    self.interrupt_signal = False
                continue

        all_output: list[tuple[str, str]] = []
        for iopub_msg in msg_list:
            if iopub_msg["msg_type"] == "stream":
                if iopub_msg["content"].get("name") == "stdout":
                    output = iopub_msg["content"]["text"]
                    all_output.append(("stdout", output))
            elif iopub_msg["msg_type"] == "execute_result":
                if "data" in iopub_msg["content"]:
                    if "text/plain" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/plain"]
                        all_output.append(("execute_result_text", output))
                    if "text/html" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/html"]
                        all_output.append(("execute_result_html", output))
                    if "image/png" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/png"]
                        all_output.append(("execute_result_png", output))
                    if "image/jpeg" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/jpeg"]
                        all_output.append(("execute_result_jpeg", output))
            elif iopub_msg["msg_type"] == "display_data":
                if "data" in iopub_msg["content"]:
                    if "text/plain" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/plain"]
                        all_output.append(("display_text", output))
                    if "text/html" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/html"]
                        all_output.append(("display_html", output))
                    if "image/png" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/png"]
                        all_output.append(("display_png", output))
                    if "image/jpeg" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/jpeg"]
                        all_output.append(("display_jpeg", output))
            elif iopub_msg["msg_type"] == "error":
                # TODO: 正确返回格式
                if "traceback" in iopub_msg["content"]:
                    output = "\n".join(iopub_msg["content"]["traceback"])
                    cleaned_output = self.delete_color_control_char(output)
                    all_output.append(("error", cleaned_output))
        return all_output

    async def get_created_images(self, section: str) -> list[str]:
        """获取新创建的图片列表"""
        current_images = set()
        files = os.listdir(self.work_dir)
        for file in files:
            if file.endswith((".png", ".jpg", ".jpeg")):
                current_images.add(file)

        # 计算新增的图片
        new_images = current_images - self.last_created_images

        # 更新last_created_images为当前的图片集合
        self.last_created_images = current_images

        logger.info(f"新创建的图片列表: {new_images}")
        return list(new_images)  # 最后转换为list返回

    async def cleanup(self):
        # 关闭内核
        assert self.kc is not None
        assert self.km is not None
        self.kc.shutdown()
        logger.info("关闭内核")
        self.km.shutdown_kernel()

    def send_interrupt_signal(self):
        self.interrupt_signal = True

    def _interrupt_kernel(self) -> None:
        """尽力中断当前内核。

        中断失败只记录日志而不抛出异常，避免在超时/取消处理路径上二次失败。
        """
        try:
            if self.km is not None:
                self.km.interrupt_kernel()
        except Exception as e:
            logger.error(f"中断内核失败: {e}")

    async def restart_jupyter_kernel(self):
        """Restart the Jupyter kernel and recreate the work directory."""
        assert self.kc is not None
        self.kc.shutdown()
        # 设置 UTF-8 编码环境，避免 Windows 中文环境下 GBK 编码导致的乱码问题
        kernel_env = os.environ.copy()
        kernel_env["PYTHONIOENCODING"] = "utf-8"
        kernel_env["PYTHONUTF8"] = "1"
        # start_new_kernel 会阻塞等待内核就绪，走线程池避免阻塞事件循环
        self.km, self.kc = await asyncio.to_thread(
            jupyter_client.manager.start_new_kernel,
            kernel_name="python3",
            env=kernel_env,
        )
        self.interrupt_signal = False
        self._create_work_dir()
        await self._pre_execute_code()

    def _create_work_dir(self):
        """Ensure the working directory exists after a restart."""
        os.makedirs(self.work_dir, exist_ok=True)
