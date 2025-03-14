import base64
import logging
import uuid
from typing import List, Optional

from asgiref.sync import async_to_sync
from django.conf import settings
from langrocks.client import WebBrowser
from langrocks.common.models.files import File
from langrocks.common.models.web_browser import (
    WebBrowserCommand,
    WebBrowserCommandType,
    WebBrowserContent,
    WebBrowserDownload,
)
from pydantic import BaseModel, Field, field_validator

from llmstack.apps.schemas import OutputTemplate
from llmstack.common.blocks.base.schema import StrEnum
from llmstack.processors.providers.api_processor_interface import (
    ApiProcessorInterface,
    ApiProcessorSchema,
)

logger = logging.getLogger(__name__)


class BrowserInstructionType(StrEnum):
    CLICK = "Click"
    TYPE = "Type"
    WAIT = "Wait"
    GOTO = "Goto"
    COPY = "Copy"
    TERMINATE = "Terminate"
    ENTER = "Enter"
    SCROLLX = "Scrollx"
    SCROLLY = "Scrolly"
    MOUSE_MOVE = "Mousemove"
    KEY = "Key"


class BrowserInstruction(BaseModel):
    type: BrowserInstructionType
    selector: Optional[str] = None
    data: Optional[str] = None

    @field_validator("type")
    def validate_type(cls, v):
        return v.lower().capitalize()


class BrowserRemoteSessionData(BaseModel):
    ws_url: str = Field(
        description="Websocket URL to connect to",
    )


class StaticWebBrowserConfiguration(ApiProcessorSchema):
    connection_id: Optional[str] = Field(
        default=None,
        description="Connection to use",
        json_schema_extra={"advanced_parameter": False, "widget": "connection"},
    )
    stream_video: bool = Field(
        description="Stream video of the browser",
        default=False,
    )
    timeout: int = Field(
        description="Timeout in seconds",
        default=10,
        ge=1,
        le=100,
    )
    tags_to_extract: List[str] = Field(
        description="Tags to extract. e.g. ['a', 'img']",
        default=[],
        json_schema_extra={"advanced_parameter": True},
    )
    extract_html: bool = Field(
        description="Extract HTML",
        default=False,
    )
    capture_screenshot: bool = Field(
        description="Capture screenshot",
        default=True,
    )
    capture_session_video: bool = Field(
        description="Capture session video",
        default=False,
    )


class StaticWebBrowserInput(ApiProcessorSchema):
    url: str = Field(
        description="URL to visit",
    )
    instructions: List[BrowserInstruction] = Field(
        ...,
        description="Instructions to execute",
    )


class StaticWebBrowserFile(File):
    data: str = Field(
        default="",
        description="File data as an objref",
    )


class StaticWebBrowserDownload(WebBrowserDownload):
    file: StaticWebBrowserFile = Field(
        default=None,
        description="File",
    )


class StaticWebBrowserContent(WebBrowserContent):
    screenshot: Optional[str] = Field(
        default=None,
        description="Screenshot of the result",
    )
    downloads: Optional[List[StaticWebBrowserDownload]] = Field(
        default=None,
        description="Downloads",
    )


class StaticWebBrowserOutput(ApiProcessorSchema):
    text: str = Field(default="", description="Text of the result")
    video: Optional[str] = Field(
        default=None,
        description="Video of the result",
    )
    content: Optional[StaticWebBrowserContent] = Field(
        default=None,
        description="Content of the result including text, buttons, links, inputs, textareas and selects",
    )
    session: Optional[BrowserRemoteSessionData] = Field(
        default=None,
        description="Session data from the browser",
    )
    steps: List[str] = Field(
        default=[],
        description="Steps taken to complete the task",
    )
    videos: Optional[List[StaticWebBrowserFile]] = Field(
        default=None,
        description="Videos from the browser session. Make sure to TERMINATE the browser to capture the video",
    )


class StaticWebBrowser(
    ApiProcessorInterface[StaticWebBrowserInput, StaticWebBrowserOutput, StaticWebBrowserConfiguration],
):
    """
    Browse a given URL
    """

    @staticmethod
    def name() -> str:
        return "Static Web Browser"

    @staticmethod
    def slug() -> str:
        return "static_web_browser"

    @staticmethod
    def description() -> str:
        return "Visit a URL and perform actions. Copy, Wait, Goto and Click are the valid instruction types"

    @staticmethod
    def provider_slug() -> str:
        return "promptly"

    @classmethod
    def get_output_template(cls) -> Optional[OutputTemplate]:
        return OutputTemplate(
            markdown="""
{% if session %}
### Live Browser Session
<promptly-web-browser-embed wsUrl="{{session.ws_url}}"></promptly-web-browser-embed>
{% endif %}
{% if videos %}
### Videos
{% for video in videos %}
<pa-asset url="{{video.data}}" type="{{video.mime_type}}"></pa-asset>
{% endfor %}
{% endif %}
{% if content.screenshot %}
### Screenshot
<pa-asset url="{{content.screenshot}}" type="image/png"></pa-asset>
{% endif %}
{{text}}
""",
            jsonpath="$.text",
        )

    def _web_browser_instruction_to_command(self, instruction: BrowserInstruction) -> WebBrowserCommand:
        instruction_type = instruction.type
        if instruction.type == BrowserInstructionType.GOTO.value:
            instruction_type = WebBrowserCommandType.GOTO
        elif instruction.type == BrowserInstructionType.CLICK.value:
            instruction_type = WebBrowserCommandType.CLICK
        elif instruction.type == BrowserInstructionType.WAIT.value:
            instruction_type = WebBrowserCommandType.WAIT
        elif instruction.type == BrowserInstructionType.COPY.value:
            instruction_type = WebBrowserCommandType.COPY
        elif instruction.type == BrowserInstructionType.SCROLLX.value:
            instruction_type = WebBrowserCommandType.SCROLL_X
        elif instruction.type == BrowserInstructionType.SCROLLY.value:
            instruction_type = WebBrowserCommandType.SCROLL_Y
        elif instruction.type == BrowserInstructionType.TERMINATE.value:
            instruction_type = WebBrowserCommandType.TERMINATE
        elif instruction.type == BrowserInstructionType.ENTER.value:
            instruction_type = WebBrowserCommandType.ENTER
        elif instruction.type == BrowserInstructionType.TYPE.value:
            instruction_type = WebBrowserCommandType.TYPE
        elif instruction.type == BrowserInstructionType.MOUSE_MOVE.value:
            instruction_type = WebBrowserCommandType.MOUSE_MOVE
        elif instruction.type == BrowserInstructionType.KEY.value:
            instruction_type = WebBrowserCommandType.KEY

        return WebBrowserCommand(
            command_type=instruction_type, data=(instruction.data or ""), selector=(instruction.selector or "")
        )

    def process(self) -> dict:
        output_stream = self._output_stream
        browser_response = None
        session_videos = []
        updated_session_data = None

        with WebBrowser(
            f"{settings.RUNNER_HOST}:{settings.RUNNER_PORT}",
            interactive=self._config.stream_video,
            capture_screenshot=self._config.capture_screenshot,
            html=self._config.extract_html,
            tags_to_extract=self._config.tags_to_extract,
            session_data=(
                self._env["connections"][self._config.connection_id]["configuration"]["_storage_state"]
                if self._config.connection_id
                else ""
            ),
            record_video=self._config.capture_session_video,
            persist_session=bool(self._config.connection_id),
        ) as web_browser:
            if self._config.stream_video and web_browser.get_wss_url():
                async_to_sync(
                    output_stream.write,
                )(
                    StaticWebBrowserOutput(
                        session=BrowserRemoteSessionData(
                            ws_url=web_browser.get_wss_url(),
                        ),
                    ),
                )

            browser_response = web_browser.run_commands(
                [
                    WebBrowserCommand(
                        command_type=WebBrowserCommandType.GOTO,
                        data=self._input.url,
                    )
                ]
                + list(map(self._web_browser_instruction_to_command, self._input.instructions))
            )

            if self._config.capture_session_video:
                session_videos = web_browser.get_videos()

            if self._config.connection_id:
                updated_session_data = web_browser.terminate()

        if browser_response:
            output_text = browser_response.text or "\n".join(
                list(map(lambda entry: entry.output, browser_response.command_outputs))
            )
            browser_content = StaticWebBrowserContent.model_validate(
                browser_response.model_dump(exclude=("screenshot", "downloads"))
            )
            screenshot_asset = None
            if browser_response.screenshot:
                screenshot_asset = self._upload_asset_from_url(
                    f"data:image/png;name={str(uuid.uuid4())};base64,{base64.b64encode(browser_response.screenshot).decode('utf-8')}",
                    mime_type="image/png",
                )
            browser_content.screenshot = screenshot_asset.objref if screenshot_asset else None

            if browser_response.downloads:
                swb_downloads = []
                for download in browser_response.downloads:
                    # Create an objref for the file data
                    file_data = self._upload_asset_from_url(
                        f"data:{download.file.mime_type};name={download.file.name};base64,{base64.b64encode(download.file.data).decode('utf-8')}",
                        mime_type=download.file.mime_type,
                    )

                    swb_download = StaticWebBrowserDownload(
                        url=download.url,
                        file=StaticWebBrowserFile(
                            name=download.file.name,
                            data=file_data.objref,
                            mime_type=download.file.mime_type,
                        ),
                    )
                    swb_downloads.append(swb_download)
                browser_content.downloads = swb_downloads
            async_to_sync(self._output_stream.write)(StaticWebBrowserOutput(text=output_text, content=browser_content))

        if session_videos:
            encoded_videos = []
            for video in session_videos:
                encoded_videos.append(
                    StaticWebBrowserFile(
                        name=video.name,
                        data=self._upload_asset_from_url(
                            f"data:{video.mime_type};name={video.name};base64,{base64.b64encode(video.data).decode('utf-8')}",
                            mime_type=video.mime_type,
                        ).objref,
                        mime_type=video.mime_type,
                    )
                )
            async_to_sync(self._output_stream.write)(StaticWebBrowserOutput(videos=encoded_videos))

        output = output_stream.finalize()

        if self._config.connection_id and updated_session_data:
            self.update_connection(
                {
                    **self._env["connections"][self._config.connection_id],
                    "configuration": {
                        **self._env["connections"][self._config.connection_id]["configuration"],
                        "_storage_state": updated_session_data,
                    },
                }
            )

        return output
