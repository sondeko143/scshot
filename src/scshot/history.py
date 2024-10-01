from copy import copy
from dataclasses import asdict, dataclass, field
from functools import lru_cache
import hashlib
import json
from logging import Logger
from pathlib import Path
from threading import RLock


class HistoryNotFound(Exception):
    pass

class HistoryFileError(Exception):
    pass


@dataclass
class History:
    original: str
    translated: str
    tlc: str
    slc: str


@dataclass
class HistoryDB:
    db_dir: Path
    logger: Logger
    locks: dict[str, RLock] = field(default_factory=dict)

    def __post_init__(self):
        self.db_dir = self.db_dir.expanduser()
        if not self.db_dir.exists():
            self.db_dir.mkdir(exist_ok=True, parents=True)
        else:
            if not self.db_dir.is_dir():
                raise ValueError(f"{self.db_dir} is not a directory")

    def insert(self, history: History):
        file = self._find_file(text=history.original, tlc=history.tlc)
        lock = self.locks.get(str(file))
        if  not lock:
            self.locks[str(file)] = RLock()
            lock = self.locks[str(file)]
        with lock:
            try:
                self.get(history.original, history.tlc)
            except HistoryNotFound:
                with file.open("r") as f:
                    objects = json.load(f)
                with file.open("w") as f:
                    objects.append(asdict(history))
                    json.dump(objects, f)
                self.logger.debug("wrote to %s", file)
            except HistoryFileError:
                self.logger.debug("history file corrupted or empty: %s", file)
                with file.open("w") as f:
                    json.dump([asdict(history)], f)

    @lru_cache(maxsize=256)
    def get(self, text: str, tlc: str) -> History:
        file = self._find_file(text=text, tlc=tlc)
        try:
            with file.open("r") as f:
                objects = json.load(f)
                return next(
                    History(**object)
                    for object in objects
                    if object["original"] == text and object["tlc"] == tlc
                )
        except (json.JSONDecodeError, KeyError, TypeError, FileNotFoundError) as e:
            self.logger.debug("history file corrupted or empty: %s, %s", file, e)
            raise HistoryFileError
        except StopIteration:
            raise HistoryNotFound

    def _find_file(self, text: str, tlc: str):
        return self.find_file(self.db_dir, text, tlc)

    def __hash__(self) -> int:
        return hash(self.db_dir)

    @staticmethod
    def find_file(db_dir: Path, text: str, tlc: str):
        dir = db_dir / Path(tlc)
        if not dir.exists():
            dir.mkdir(parents=True, exist_ok=True)
        file = dir / Path(HistoryDB.crude_hash(copy(text)))
        return file

    @staticmethod
    def crude_hash(w: str):
        h = hashlib.new("md5")
        h.update(w.encode())
        return h.hexdigest()[:1]
