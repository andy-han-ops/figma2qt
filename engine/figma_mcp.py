#!/usr/bin/env python3
"""Minimal Figma MCP client for the local Dev Mode server.

The client speaks JSON-RPC over HTTP with SSE responses, which is what the
Figma Dev Mode MCP server at http://127.0.0.1:3845/mcp expects.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

import requests

DEFAULT_URL = "http://127.0.0.1:3845/mcp"
URL_PATTERN = re.compile(r"https?://[^\s\"'<>)]+")


class FigmaMCPError(RuntimeError):
    pass


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


class FigmaMCP:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 60.0) -> None:
        self.url = url
        self.timeout = timeout
        self.session = requests.Session()
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}
        self._request_id = 0
        self.initialize()

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        return headers

    @staticmethod
    def _parse_sse(text: str) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        data_lines: list[str] = []
        in_data = False

        def flush() -> None:
            nonlocal in_data
            if not data_lines:
                in_data = False
                return
            payload = "\n".join(data_lines)
            data_lines.clear()
            in_data = False
            for candidate in (payload, payload.strip()):
                try:
                    messages.append(json.loads(candidate, strict=False))
                    return
                except json.JSONDecodeError:
                    continue
            # Some servers put a large JSON object inside extra SSE metadata.
            start = payload.find("{")
            end = payload.rfind("}")
            if start != -1 and end > start:
                try:
                    messages.append(
                        json.loads(payload[start : end + 1], strict=False)
                    )
                except json.JSONDecodeError:
                    pass

        for raw_line in text.splitlines():
            line = raw_line.rstrip("\r")
            if line.startswith("event:"):
                flush()
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
                in_data = True
                continue
            if line.startswith("id:") or line.startswith("retry:"):
                continue
            if in_data:
                # The Figma server emits raw newlines inside JSON string
                # values, so continuation lines are part of the data payload.
                data_lines.append(line)
        flush()

        if messages:
            for message in reversed(messages):
                if "result" in message or "error" in message:
                    return message
            return messages[-1]

        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError as error:
            raise FigmaMCPError(
                f"unparseable MCP response: {text[:500]}"
            ) from error

    def _post(self, payload: dict[str, Any], expect_response: bool = True) -> Any:
        response = self.session.post(
            self.url,
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self.session_id = session_id
        text = response.content.decode("utf-8", errors="replace")
        if not response.ok:
            raise FigmaMCPError(
                f"MCP HTTP {response.status_code}: {text[:500]}"
            )
        if not expect_response:
            return None
        message = self._parse_sse(text)
        if "error" in message:
            raise FigmaMCPError(json.dumps(message["error"], ensure_ascii=False))
        return message.get("result", message)

    def initialize(self) -> dict[str, Any]:
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "figma2qt", "version": "1.0"},
                },
            }
        )
        self._post(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            expect_response=False,
        )
        self.server_info = result
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            }
        )
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if result.get("isError"):
            raise FigmaMCPError(
                f"tool {name} failed: " + json.dumps(result, ensure_ascii=False)
            )
        return result

    def get_metadata(
        self, file_key: str, node_id: str | None = None
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"fileKey": file_key}
        if node_id:
            arguments["nodeId"] = node_id
        return self.call_tool("get_metadata", arguments)

    def get_design_context(
        self,
        file_key: str,
        node_id: str,
        asset_dir: Path | None = None,
        client_languages: str = "cpp",
        client_frameworks: str = "qt",
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "fileKey": file_key,
            "nodeId": node_id,
            "clientLanguages": client_languages,
            "clientFrameworks": client_frameworks,
            "excludeScreenshot": True,
        }
        if asset_dir is not None:
            arguments["dirForAssetWrites"] = str(Path(asset_dir).resolve())
        return self.call_tool("get_design_context", arguments)

    def get_screenshot(
        self,
        file_key: str,
        node_id: str,
        max_dimension: int = 1920,
        contents_only: bool = True,
    ) -> dict[str, Any]:
        return self.call_tool(
            "get_screenshot",
            {
                "fileKey": file_key,
                "nodeId": node_id,
                "maxDimension": max_dimension,
                "contentsOnly": contents_only,
            },
        )

    @staticmethod
    def find_urls(result: Any) -> list[str]:
        urls: list[str] = []
        for text in _iter_strings(result):
            urls.extend(URL_PATTERN.findall(text))
        return urls

    @staticmethod
    def find_image_urls(result: Any) -> list[str]:
        urls = FigmaMCP.find_urls(result)
        preferred = [
            url
            for url in urls
            if any(
                token in url.lower()
                for token in (".png", ".jpg", ".jpeg", ".webp")
            ) and "developers.figma.com" not in url
        ]
        return preferred

    @staticmethod
    def find_base64_images(result: Any) -> list[str]:
        images: list[str] = []
        if isinstance(result, dict):
            mime = str(result.get("mimeType", ""))
            data = result.get("data")
            if isinstance(data, str) and mime.startswith("image/"):
                images.append(data)
            for value in result.values():
                images.extend(FigmaMCP.find_base64_images(value))
        elif isinstance(result, list):
            for value in result:
                images.extend(FigmaMCP.find_base64_images(value))
        return images

    def download(self, url: str, out_path: Path) -> Path:
        out_path = Path(out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        response = self.session.get(url, timeout=self.timeout)
        if not response.ok:
            raise FigmaMCPError(
                f"asset download failed: HTTP {response.status_code} {url}"
            )
        out_path.write_bytes(response.content)
        return out_path

    def fetch_reference(
        self,
        file_key: str,
        node_id: str,
        out_path: Path,
        *,
        max_dimension: int = 1920,
        metadata_path: Path | None = None,
    ) -> dict[str, Any]:
        metadata = self.get_metadata(file_key, node_id)
        if metadata_path:
            metadata_path = Path(metadata_path).resolve()
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        screenshot = self.get_screenshot(
            file_key,
            node_id,
            max_dimension=max_dimension,
            contents_only=False,
        )

        out_path = Path(out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        urls = self.find_image_urls(screenshot)
        if urls:
            self.download(urls[0], out_path)
            return {
                "method": "url",
                "url": urls[0],
                "path": str(out_path),
                "metadata": str(metadata_path) if metadata_path else "",
            }

        images = self.find_base64_images(screenshot)
        if images:
            out_path.write_bytes(base64.b64decode(images[0]))
            return {
                "method": "base64",
                "path": str(out_path),
                "metadata": str(metadata_path) if metadata_path else "",
            }

        raise FigmaMCPError(
            "get_screenshot returned neither a URL nor a base64 image: "
            + json.dumps(screenshot, ensure_ascii=False)[:1000]
        )


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--metadata", action="store_true")
    parser.add_argument("--design-context", action="store_true")
    parser.add_argument("--screenshot", action="store_true")
    parser.add_argument("--reference", action="store_true")
    parser.add_argument("--file-key", default="")
    parser.add_argument("--node-id", default="")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--metadata-out", type=Path)
    parser.add_argument("--asset-dir", type=Path)
    parser.add_argument("--max-dimension", type=int, default=1920)
    args = parser.parse_args(argv)

    client = FigmaMCP(args.url)

    if args.probe:
        info = client.server_info.get("serverInfo", {})
        result = {
            "server": {
                "name": info.get("name", ""),
                "version": info.get("version", ""),
            },
            "tools": [
                tool.get("name", "") for tool in client.list_tools()
            ],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not args.file_key:
        parser.error("--file-key is required")

    if args.metadata:
        result = client.get_metadata(args.file_key, args.node_id or None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.design_context:
        if not args.node_id:
            parser.error("--node-id is required for --design-context")
        result = client.get_design_context(
            args.file_key,
            args.node_id,
            asset_dir=args.asset_dir,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.screenshot or args.reference:
        if not args.node_id or not args.out:
            parser.error("--node-id and --out are required")
        if args.reference:
            result = client.fetch_reference(
                args.file_key,
                args.node_id,
                args.out,
                max_dimension=args.max_dimension,
                metadata_path=args.metadata_out,
            )
        else:
            screenshot = client.get_screenshot(
                args.file_key,
                args.node_id,
                max_dimension=args.max_dimension,
            )
            urls = client.find_image_urls(screenshot)
            if urls:
                client.download(urls[0], args.out)
                result = {"method": "url", "url": urls[0], "path": str(args.out)}
            else:
                images = client.find_base64_images(screenshot)
                if not images:
                    raise FigmaMCPError("no image URL or base64 data found")
                args.out.write_bytes(base64.b64decode(images[0]))
                result = {"method": "base64", "path": str(args.out)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
