import json
from logging import getLogger
from pathlib import Path

from scshot.history import History, HistoryDB, HistoryFileError


def test_hash():
    hash_value = HistoryDB.crude_hash("hello world")
    assert hash_value == "5"


def test_partially_correct_history():
    db_dir = Path("tests/db")
    text = "hello world"
    tlc = "ja-JP"
    file = HistoryDB.find_file(db_dir, text, tlc=tlc)
    with file.open("w") as f:
        object = [{
            "original": text,
            "translated": "こんにちは",
            "tlc": tlc,
        }]
        json.dump(object, f)
    db = HistoryDB(db_dir=Path("tests/db"), logger=getLogger())
    try:
        db.get(text, tlc)
        assert False, "should raise file error"
    except HistoryFileError:
        pass


def test_get_correct_history():
    db_dir = Path("tests/db")
    text = "hello world"
    tlc = "ja-JP"
    slc = "en-US"
    file = HistoryDB.find_file(db_dir, text, tlc=tlc)
    with file.open("w") as f:
        object = [{"original": text, "translated": "こんにちは", "tlc": tlc, "slc": slc}]
        json.dump(object, f)
    db = HistoryDB(db_dir=Path("tests/db"), logger=getLogger())
    history = db.get(text, tlc)
    assert history == History(original=text, translated="こんにちは", tlc=tlc, slc=slc)
