import argparse
import logging
import sys
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
from html import unescape
from io import BytesIO
from logging import getLogger
from os import system
from typing import Any, cast

import bettercam  # type: ignore
import tomllib
import win32gui
from google.cloud import vision  # type: ignore
from google.cloud.translate_v3 import TranslateTextRequest, TranslationServiceClient
from PIL import Image

TRANSLATION_CACHE: dict[str, str] = {}

logger = getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


@dataclass
class Settings:
    text_ignore: list[str]
    target_window_title: str
    target_language_code: str
    language_codes_display: list[str]
    language_codes_ignore: list[str]
    display_code: str
    google_translate_api_project_name: str


@dataclass
class Output:
    original: str
    translated: str
    left: int
    top: int
    right: int
    bottom: int


def translate_text(text: str, settings: Settings):
    logger.debug("start thread: %s", text)
    if text in settings.text_ignore:
        return [text, ""]
    if text.isdigit():
        return [text, ""]
    if text in TRANSLATION_CACHE:
        return [text, TRANSLATION_CACHE[text]]

    client = TranslationServiceClient()
    request = TranslateTextRequest(
        contents=[text],
        target_language_code=settings.target_language_code,
        parent=settings.google_translate_api_project_name,
    )
    try:
        logger.debug("request translation: %s", text)
        result = client.translate_text(request=request)  # type: ignore
    except CancelledError:
        logger.debug("canceled")
        return [text, ""]
    result = result.translations[0]
    dlc = result.detected_language_code
    translated = cast(str, unescape(result.translated_text))  # type: ignore
    logger.debug("translated: [%s] %s", dlc, translated)
    if dlc in settings.language_codes_display:
        TRANSLATION_CACHE[text] = translated
        return [text, translated]
    TRANSLATION_CACHE[text] = ""
    return [text, ""]


def bulk_translate(texts: list[str], settings: Settings):
    with ThreadPoolExecutor() as executor:
        futures: list[Future[list[str]]] = []
        try:
            futures = [
                executor.submit(
                    translate_text,
                    text,
                    settings,
                )
                for text in texts
            ]
            results = [future.result() for future in futures]
        except KeyboardInterrupt:
            logger.debug("interrupted executor")
            for future in futures:
                logger.debug("cancel future %s", future)
                future.cancel()
            executor.shutdown()
            for thread in executor._threads:
                logger.debug("join thread %s", thread)
                thread.join()
            raise
    return [{"original": t, "translated": translated} for t, translated in results]


def detect_text(image: bytes, settings: Settings) -> list[Output]:
    """Detects text in the file."""

    client = vision.ImageAnnotatorClient()
    client_image = vision.Image(content=image)

    response: Any = client.text_detection(image=client_image)  # type: ignore
    document = response.full_text_annotation
    if not document:
        return []
    block_texts: list[str] = []
    bounds: dict[str, list[int]] = {}
    for page in document.pages:
        for block in page.blocks:
            block_text = ""
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    if any(
                        l.language_code in settings.language_codes_ignore
                        and l.confidence == 1
                        for l in word.property.detected_languages
                    ):
                        continue
                    for symbol in word.symbols:
                        block_text += symbol.text
            if block_text:
                xs = [int(v.x) for v in block.bounding_box.vertices]
                ys = [int(v.y) for v in block.bounding_box.vertices]
                left = min(xs)
                top = min(ys)
                right = max(xs)
                bottom = max(ys)
                bounds[block_text] = [left, top, right, bottom]
                block_texts.append(block_text)
    logger.debug("detect %s", len(block_texts))
    translateds = bulk_translate(block_texts, settings=settings)
    translateds = [t for t in translateds if t["translated"]]
    outputs = [
        Output(
            original=translated["original"],
            translated=translated["translated"],
            left=bounds[translated["original"]][0],
            top=bounds[translated["original"]][1],
            right=bounds[translated["original"]][2],
            bottom=bounds[translated["original"]][3],
        )
        for translated in translateds
    ]

    return outputs


def clear():
    system("cls")


def display_results(results: list[Output], display_code: str):
    exec(
        display_code,
        {"__builtins__": None},
        {"print": print, "results": results, "clear": clear},
    )


def translate_window(hwnd: int, settings: Settings):
    camera = bettercam.create()  # type: ignore
    rect = win32gui.GetWindowRect(hwnd)
    frame = camera.grab(rect)  # type: ignore
    if frame is None:
        return
    image = Image.fromarray(frame)  # type: ignore
    with BytesIO() as image_content:
        image.save(image_content, format="PNG")
        results = detect_text(image_content.getvalue(), settings=settings)
    display_results(results, settings.display_code)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="scshot", description="Take a screenshot and translate texts"
    )
    parser.add_argument("-c", "--config", required=True)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-l", "--loop", action="store_true")
    args = parser.parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    with open(args.config, "rb") as f:
        config = tomllib.load(f)
        settings = Settings(**config)

    while True:
        target_windows: list[int] = []

        def get_specific_window_callback(hwnd: int, _: Any):
            name = win32gui.GetWindowText(hwnd)
            if name == settings.target_window_title:
                target_windows.append(hwnd)

        win32gui.EnumWindows(get_specific_window_callback, None)
        try:
            for window in target_windows:
                translate_window(window, settings=settings)
        except KeyboardInterrupt:
            logger.info("interrupted")
            break
        if not args.loop:
            break
    return 0
