import argparse
import logging
import os
import sys
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from html import unescape
from io import BytesIO
from logging import getLogger
from pathlib import Path
from time import sleep
from typing import Any, cast

import bettercam  # type: ignore
import tomllib
import win32gui
from google.cloud import vision  # type: ignore
from google.cloud.translate_v3 import TranslateTextRequest, TranslationServiceClient
from PIL import Image

from scshot.history import History, HistoryDB, HistoryFileError, HistoryNotFound

TRANSLATION_CACHE: dict[str, str] = {}

logger = getLogger(__name__)


@dataclass
class Settings:
    google_translate_api_project_name: str
    target_window_title: str | None = None
    text_ignore: list[str] = field(default_factory=list)
    target_language_code: str = "en-US"
    language_codes_display: list[str] = field(default_factory=lambda: ["en-US"])
    language_codes_ignore: list[str] = field(default_factory=list)
    history_db_dir: Path = Path("~/.cache/scshot/db")
    display_code: str = """
clear()
for result in results:
    print(f"{result.original}\\n->{result.translated}\\n")
"""


@dataclass
class Output:
    original: str
    translated: str
    left: int
    top: int
    right: int
    bottom: int


def translate_text(text: str, settings: Settings, db: HistoryDB):
    logger.debug("start thread: %s", text)
    if text in settings.text_ignore:
        return [text, ""]
    if text.isdigit():
        return [text, ""]

    try:
        history = db.get(text=text, tlc=settings.target_language_code)
        logger.debug("found in histories: %s, %s", text, history.slc)
        if history.slc in settings.language_codes_display:
            return [text, history.translated]
        return [text, ""]
    except (HistoryNotFound, HistoryFileError):
        pass

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
    db.insert(
        History(
            original=text,
            translated=translated,
            tlc=settings.target_language_code,
            slc=dlc,
        )
    )
    if dlc in settings.language_codes_display:
        return [text, translated]
    logger.debug("Ignore text %s because %s is not display lc list.", text, dlc)
    return [text, ""]


def bulk_translate(texts: list[str], settings: Settings, db: HistoryDB):
    with ThreadPoolExecutor() as executor:
        futures: list[Future[list[str]]] = []
        try:
            futures = [
                executor.submit(translate_text, text, settings, db) for text in texts
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


def detect_text(image: bytes, settings: Settings, db: HistoryDB) -> list[Output]:
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
            block_text: list[str] = []
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    if any(
                        l.language_code in settings.language_codes_ignore
                        and l.confidence == 1
                        for l in word.property.detected_languages
                    ):
                        continue
                    for symbol in word.symbols:
                        block_text.append(symbol.text)
            if block_text:
                joined_text = "".join(block_text)
                xs = [int(v.x) for v in block.bounding_box.vertices]
                ys = [int(v.y) for v in block.bounding_box.vertices]
                left = min(xs)
                top = min(ys)
                right = max(xs)
                bottom = max(ys)
                bounds[joined_text] = [left, top, right, bottom]
                block_texts.append(joined_text)
    logger.debug("detect %s", len(block_texts))
    translateds = bulk_translate(block_texts, settings=settings, db=db)
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
    os.system("cls")


def writeln(text: str, mode: str = "a"):
    with open("index.html", mode, encoding="utf-8") as f:
        f.write(text + "\n")


def display_results(results: list[Output], display_code: str):
    exec(
        display_code,
        {"__builtins__": None},
        {"print": print, "writeln": writeln, "results": results, "clear": clear},
    )


def translate_window(hwnd: int, settings: Settings, db: HistoryDB):
    camera = cast(bettercam.BetterCam, bettercam.create())  # type: ignore
    cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
    sl, st = win32gui.ClientToScreen(hwnd, (cl, ct))
    frame = camera.grab((sl, st, sl + cr, st + cb))  # type: ignore
    if frame is None:
        return
    image = Image.fromarray(frame)  # type: ignore
    with BytesIO() as image_content:
        image.save(image_content, format="PNG")
        results = detect_text(image_content.getvalue(), settings=settings, db=db)
    display_results(results, settings.display_code)


def get_window_handlers(target_window_title: str | None):
    target_windows: list[int] = []

    if target_window_title:

        def get_specific_window_callback(hwnd: int, _: Any):
            name = win32gui.GetWindowText(hwnd)
            logger.debug(name)
            if name == target_window_title:
                target_windows.append(hwnd)

        win32gui.EnumWindows(get_specific_window_callback, None)
    else:
        target_windows.append(win32gui.GetForegroundWindow())
    return target_windows


def main() -> int:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    parser = argparse.ArgumentParser(
        prog="scshot", description="Take a screenshot and translate texts"
    )
    parser.add_argument("-c", "--config", required=True)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-l", "--loop", default=-1.0, type=float)
    parser.add_argument("-w", "--window", type=str)
    args = parser.parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    with open(args.config, "rb") as f:
        config = tomllib.load(f)
        settings = Settings(**config)
        if args.window:
            settings.target_window_title = args.window

    db = HistoryDB(db_dir=settings.history_db_dir, logger=logger)
    while True:
        target_windows = get_window_handlers(settings.target_window_title)
        try:
            for window in target_windows:
                translate_window(window, settings=settings, db=db)
        except KeyboardInterrupt:
            logger.info("interrupted")
            break
        if args.loop < 0:
            break
        logger.debug("sleep %s", args.loop)
        sleep(args.loop)
    return 0
